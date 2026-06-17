"""
ui_components.py - All Streamlit UI rendering components, dialogs, and the image grid
"""

import base64
import concurrent.futures
import json
import logging
import re
import zipfile
import gc
from io import BytesIO

import orjson
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from constants import GRID_COLS, JUMIA_COLORS
from data_utils import (
    _get_image_from_zip, clean_category_code, df_hash, format_local_price, load_df_parquet
)
from export_utils import generate_smart_export, prepare_full_data_merged

logger = logging.getLogger(__name__)

_SVG_RAW = "<svg xmlns='http://www.w3.org/2000/svg' width='150' height='150'><rect width='150' height='150' fill='#f0f0f0'/><text x='75' y='75' text-anchor='middle' dominant-baseline='central' font-size='12' font-family='sans-serif' fill='#999'>No Image</text></svg>"
_NO_IMAGE_SVG = f"data:image/svg+xml;base64,{base64.b64encode(_SVG_RAW.encode('utf-8')).decode('utf-8')}"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

PREFETCH_DISPLAY_COLUMNS = {
    "Wrong Category": ["Category_Check_Status", "Category_Check_Rejection_Reason", "Initial_Category_Path", "Suggested_Categories", "Top1_Category", "AI_Product_Caption", "Category_Match_Score", "Top1_Score"],
    "Product Warranty": ["Warranty_Check_Status", "Warranty_Rejection_Reason", "product_warranty", "warranty_duration", "warranty_type", "warranty_address"],
    "Missing COLOR": ["Color_Check_Status", "Color_Rejection_Reason", "Color_AI_Normalized", "color", "color_family"],
    "Wrong Variation": ["Variation_Check_Status", "Variation_Rejection_Reason", "count_variations", "list_variations", "COUNT_VARIATIONS", "LIST_VARIATIONS"],
    "BRAND name repeated in NAME": ["Brand_Image_Check_Status", "Brand_Image_Check_Reason", "Brand_Detected_On_Product", "Product Name_Brand Name_Status", "Product name_Brand name_rejection reason", "Product_Name_Brand_Name_Status", "Product Name_Brand Name_Rejection_Reason"],
    "Poor images": ["Image_Quality_Check_Status", "Image_Quality_Check_Reason", "Image_Extraction_Status", "Image_Filename"],
    "Missing Weight/Volume": ["Title_Language_Check_Status", "Title_Language_Check_Reason"],
    "Duplicate product": ["Duplicate_Flag"],
    "FDA": ["FDA_Check_Status", "FDA_Rejection_Reason", "FDA"],
    "Category Check": ["Category_Check_Status", "Category_Check_Rejection_Reason", "Initial_Category_Path", "Suggested_Categories", "Top1_Category", "AI_Product_Caption", "Category_Match_Score", "Top1_Score"],
    "Warranty Check": ["Warranty_Check_Status", "Warranty_Rejection_Reason", "product_warranty", "warranty_duration", "warranty_type", "warranty_address"],
    "Color Check": ["Color_Check_Status", "Color_Rejection_Reason", "Color_AI_Normalized", "color"],
    "Variation Check": ["Variation_Check_Status", "Variation_Rejection_Reason", "count_variations", "list_variations", "COUNT_VARIATIONS", "LIST_VARIATIONS"],
    "Brand Image Check": ["Brand_Image_Check_Status", "Brand_Image_Check_Reason", "Brand_Detected_On_Product"],
    "Product Name Brand Name": ["Product Name_Brand Name_Status", "Product name_Brand name_rejection reason", "Product Name_Brand Name_Rejection_Reason"],
    "Title Language Check": ["Title_Language_Check_Status", "Title_Language_Check_Reason"],
    "Image Quality Check": ["Image_Quality_Check_Status", "Image_Quality_Check_Reason", "Image_Extraction_Status", "Image_Filename"],
}

def flag_pill_header(flag_name: str, count: int, is_zip: bool = False) -> str:
    color_map = {
        "Wrong Category": ("#fef3c7", "#d97706"), "Restricted brands": ("#fee2e2", "#dc2626"),
        "Suspected Fake product": ("#fee2e2", "#b91c1c"), "BRAND name repeated in NAME": ("#ede9fe", "#7c3aed"),
        "Duplicate product": ("#dcfce7", "#15803d"),
    }
    bg, fg = color_map.get(flag_name, ("#f3f4f6", "#374151"))
    zip_badge = ' <span style="background:linear-gradient(135deg, #3b82f6, #1d4ed8);color:white;border-radius:6px;padding:2px 8px;font-size:10px;font-weight:900;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-left:8px;">ZIP</span>' if is_zip else ""
    return f'<div style="display:flex;align-items:center;padding:10px 0;"><span style="background:{fg};color:white;border-radius:8px;padding:4px 12px;font-size:14px;font-weight:900;box-shadow:0 4px 12px {bg};">{count}</span><span style="font-size:16px;font-weight:700;margin-left:12px;color:#1f2937;">{flag_name}</span>{zip_badge}</div>'

def render_kpi_bar(final_report: pd.DataFrame):
    pass 

def render_summary_header(final_report: pd.DataFrame):
    pass 

def render_rejection_donut(final_report: pd.DataFrame):
    pass 

def _base_prefetched_title(title: str) -> str: return str(title).replace("(Prefetched)", "").strip()
def _clean_reason_value(value) -> str:
    val = str(value).strip()
    return "" if val.lower() in ("", "nan", "none", "null", "rejected", "approved", "skipped") else val
def _prefetched_reason_for_row(title: str, row, fallback="No reason provided") -> str:
    base_title = _base_prefetched_title(title)
    for col in PREFETCH_DISPLAY_COLUMNS.get(base_title, []):
        if col in row.index:
            val = _clean_reason_value(row.get(col))
            if val: return val
    for col in row.index:
        col_l = str(col).lower()
        if "reason" in col_l and col_l != "reason":
            val = _clean_reason_value(row.get(col))
            if val: return val
    return fallback

def _extract_specific_reason(comment: str) -> str:
    comment = str(comment or "").strip()
    if not comment or comment.lower() in ("nan", "none"): return ""
    m = re.search(r"\((.*)\)\s*$", comment)
    return m.group(1).strip() if m else comment

def _t(key):
    from translations import get_translation
    return get_translation(st.session_state.get("ui_lang", "en"), key)

def _clear_flag_df_selection(title: str):
    ver_key = f"df_ver_{title}"
    st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1

def _normalize_sid_set(sids) -> set: return {str(s).strip() for s in sids if str(s).strip()}

def _clear_result_caches() -> None:
    st.session_state.exports_cache.clear()
    st.session_state.display_df_cache.clear()
    st.session_state.pop("_grid_warm_urls", None)
    gc.collect()

def _drop_sids_from_post_qc_results(sid_set: set) -> None:
    results = st.session_state.get("post_qc_results", {})
    if not isinstance(results, dict) or not sid_set: return
    for flag, df in list(results.items()):
        if not isinstance(df, pd.DataFrame) or df.empty or "PRODUCT_SET_SID" not in df.columns: continue
        mask = df["PRODUCT_SET_SID"].astype(str).str.strip().isin(sid_set)
        if mask.any(): results[flag] = df.loc[~mask].copy()

def _add_sids_to_post_qc_results(sid_set: set, flag: str, comment: str = "") -> None:
    if not sid_set or not flag: return
    base_flag = str(flag).replace("(Prefetched)", "").strip()
    data = st.session_state.get("all_data_map", pd.DataFrame())
    if not isinstance(data, pd.DataFrame) or data.empty or "PRODUCT_SET_SID" not in data.columns: return
    base_rows = data[data["PRODUCT_SET_SID"].astype(str).str.strip().isin(sid_set)].copy()
    if base_rows.empty: return
    base_rows["Comment_Detail"] = comment
    results = st.session_state.setdefault("post_qc_results", {})
    existing = results.get(base_flag)
    if isinstance(existing, pd.DataFrame) and not existing.empty:
        combined = pd.concat([existing, base_rows], ignore_index=True)
        results[base_flag] = combined.drop_duplicates(subset=["PRODUCT_SET_SID"], keep="last")
    else: results[base_flag] = base_rows

def apply_status_change(
    sids, *, status: str, reason: str = "", comment: str = "", flag: str = "", is_manual: bool = True, is_zip: bool = False, sync_quick_rejects: bool = True,
) -> int:
    sid_set = _normalize_sid_set(sids)
    is_image_rej = status == "Rejected" and any(x in str(flag).lower() for x in ["image", "stretched", "blurry", "poor", "mismatch"])
    if is_image_rej:
        all_data = st.session_state.get("all_data_map")
        if all_data is not None and "PRODUCT_SET_SID" in all_data.columns and "IMAGE1" in all_data.columns:
            if "image_to_sids" not in st.session_state:
                _valid_imgs = all_data.dropna(subset=["IMAGE1"])
                st.session_state.image_to_sids = _valid_imgs.groupby("IMAGE1")["PRODUCT_SET_SID"].apply(lambda x: set(x.astype(str).str.strip())).to_dict()
            _input_sids_clean = list(sid_set)
            _target_images = all_data.loc[all_data["PRODUCT_SET_SID"].astype(str).str.strip().isin(_input_sids_clean), "IMAGE1"].dropna().unique()
            for img in _target_images:
                if img in st.session_state.image_to_sids: sid_set.update(st.session_state.image_to_sids[img])

    fr = st.session_state.get("final_report", pd.DataFrame())
    if not sid_set or not isinstance(fr, pd.DataFrame) or fr.empty or "ProductSetSid" not in fr.columns: return 0
    mask = fr["ProductSetSid"].astype(str).str.strip().isin(sid_set)
    if not mask.any(): return 0

    from datetime import datetime
    if "undo_stack" not in st.session_state: st.session_state.undo_stack = []
    old_state = fr.loc[mask, ["ProductSetSid", "Status", "Reason", "Comment", "FLAG", "Is_Manual", "Is_Zip"]].copy()
    st.session_state.undo_stack.append({"diff": old_state, "timestamp": datetime.now()})
    if len(st.session_state.undo_stack) > 10: st.session_state.undo_stack.pop(0)

    fr.loc[mask, ["Status", "Reason", "Comment", "FLAG", "Is_Manual", "Is_Zip"]] = [status, reason, comment, flag, is_manual, is_zip]

    _drop_sids_from_post_qc_results(sid_set)
    if status == "Rejected" and flag: _add_sids_to_post_qc_results(sid_set, flag, comment)

    if sync_quick_rejects:
        for sid in sid_set:
            if status == "Rejected":
                st.session_state[f"quick_rej_{sid}"] = True
                st.session_state[f"quick_rej_reason_{sid}"] = flag or comment or reason
            else:
                st.session_state.pop(f"quick_rej_{sid}", None)
                st.session_state.pop(f"quick_rej_reason_{sid}", None)

    st.session_state.data_version = st.session_state.get("data_version", 0) + 1
    _clear_result_caches()
    if len(sid_set) > 1: st.session_state["show_undo_toast"] = {"count": len(sid_set), "status": status, "time": datetime.now()}
    return int(mask.sum())

@st.dialog("Confirm Bulk Approval", icon=":material/check_circle:")
def bulk_approve_dialog(
    sids_to_process, title, subset_data, data_has_warranty_cols_check, support_files, country_validator, validation_runner,
):
    try:
        from category_matcher_engine import get_engine
        _CAT_MATCHER_AVAILABLE = True
    except ImportError: _CAT_MATCHER_AVAILABLE = False

    st.warning(f"You are about to approve **{len(sids_to_process)}** items from `{title}`.")
    _preview_cols = [c for c in ["PRODUCT_SET_SID", "NAME", "BRAND", "SELLER_NAME"] if c in subset_data.columns]
    _preview_df = subset_data[subset_data["PRODUCT_SET_SID"].isin(sids_to_process)][_preview_cols].reset_index(drop=True)
    with st.expander(f"Preview {len(_preview_df)} item(s) to be approved", expanded=len(_preview_df) <= 10):
        st.dataframe(_preview_df, hide_index=True, width='stretch')
    
    if st.button(_t("approve_btn"), type="primary", width='stretch'):
        with st.spinner("Applying…"):
            msg_approved = 0
            for sid in sids_to_process:
                sid_str = str(sid).strip()
                if apply_status_change([sid_str], status="Approved", reason="", comment="", flag="Approved by User", is_manual=True, is_zip=False):
                    msg_approved += 1

            if title == "Wrong Category" and _CAT_MATCHER_AVAILABLE:
                try:
                    engine = get_engine()
                    if engine is not None:
                        learned = 0
                        for sid in sids_to_process:
                            row = subset_data[subset_data["PRODUCT_SET_SID"].astype(str).str.strip() == str(sid)]
                            if row.empty: continue
                            name = str(row.iloc[0].get("NAME", "")).strip()
                            if not name: continue
                            engine.set_compiled_rules(st.session_state.get("compiled_json_rules", {}))
                            predicted = engine.get_category_with_boost(name)
                            if predicted and predicted.lower() not in ("nan", "none", "uncategorized", ""):
                                engine.apply_learned_correction(name, predicted, auto_save=False)
                                learned += 1
                        if learned:
                            import threading as _threading
                            _threading.Thread(target=engine.save_learning_db, daemon=True).start()
                            st.session_state.main_toasts.append(f"Engine learned {learned} correction(s) from your approvals.")
                except Exception as _le: pass

            if msg_approved > 0: st.session_state.main_toasts.append(f"{msg_approved} items successfully Approved!")
            st.session_state[f"exp_{title}"] = True
            _clear_flag_df_selection(title)
        st.rerun()

@st.fragment
def render_flag_expander(
    title, df_flagged_sids_input, data, data_has_warranty_cols_check, support_files, country_validator, validation_runner,
):
    current_fr = st.session_state.get("final_report", pd.DataFrame())
    if not current_fr.empty and "Status" in current_fr.columns:
        if title.startswith("Seller: "):
            seller_name = title.replace("Seller: ", "", 1)
            df_flagged_sids = current_fr[(current_fr["Status"] == "Rejected") & (current_fr["SellerName"] == seller_name)]
        else:
            df_flagged_sids = current_fr[(current_fr["FLAG"] == title) & (current_fr["Status"].isin(["Rejected", "Review"]))]
    else:
        df_flagged_sids = df_flagged_sids_input

    if df_flagged_sids.empty:
        st.success("All items in this group have been processed!")
        return

    _comment_map = {}
    if "Comment" in df_flagged_sids.columns and "ProductSetSid" in df_flagged_sids.columns:
        _comment_map = dict(zip(df_flagged_sids["ProductSetSid"].astype(str).str.strip(), df_flagged_sids["Comment"].astype(str)))

    try:
        from category_matcher_engine import get_engine
        _CAT_MATCHER_AVAILABLE = True
    except ImportError: _CAT_MATCHER_AVAILABLE = False

    cache_key = f"display_df_{title}_{st.session_state.get('data_version', 0)}_prefetch_context_v3"
    _is_dup_view = _base_prefetched_title(title) == "Duplicate product"
    base_display_cols = ["PRODUCT_SET_SID", "NAME", "CATEGORY", "CATEGORY_CODE", "BRAND", "COLOR", "MAIN_IMAGE", "VARIATION", "PARENTSKU", "SELLER_NAME", "SELLER_SKU", "GLOBAL_PRICE", "GLOBAL_SALE_PRICE", "PRODUCT_WARRANTY", "WARRANTY_ADDRESS", "WARRANTY_DURATION", "WARRANTY_TYPE", "COLOR_FAMILY", "MATERIAL_FAMILY", "FDA", "CATEGORY_SID", "SELLER_SID", "COUNT_VARIATIONS"]
    current_display_cols = base_display_cols.copy()
    for col in PREFETCH_DISPLAY_COLUMNS.get(_base_prefetched_title(title), []):
        if col not in current_display_cols: current_display_cols.append(col)
    if title == "Wrong Variation":
        for col in ("COUNT_VARIATIONS", "LIST_VARIATIONS"):
            if col in data.columns: current_display_cols.append(col)
    if title == "Category Max Price Exceeded": current_display_cols.append("CAT_MAX_PRICE")
    if title == "Wrong Category": current_display_cols.append("AI Suggested Category")

    possible_img_cols = ["image1", "MAIN_IMAGE_URL", "MAIN_IMAGE", "IMAGE_URL", "IMAGE1_ZIP"]
    img_col = next((c for c in possible_img_cols if c in data.columns), None)
    if img_col and img_col not in current_display_cols: current_display_cols.append(img_col)

    if cache_key not in st.session_state.display_df_cache:
        _extra_cols = [c for c in current_display_cols if c in data.columns]
        if "CATEGORY_CODE" in data.columns and "CATEGORY_CODE" not in _extra_cols: _extra_cols.append("CATEGORY_CODE")
        if "PRODUCT_SET_SID" not in _extra_cols: _extra_cols.append("PRODUCT_SET_SID")

        if "Is_Zip" not in df_flagged_sids.columns:
            df_flagged_sids = df_flagged_sids.copy()
            df_flagged_sids["Is_Zip"] = False
        if "Is_Manual" not in df_flagged_sids.columns:
            df_flagged_sids = df_flagged_sids.copy() if "Is_Zip" in df_flagged_sids.columns else df_flagged_sids
            df_flagged_sids["Is_Manual"] = False
            
        df_display = pd.merge(df_flagged_sids[["ProductSetSid", "Is_Zip"]], data, left_on="ProductSetSid", right_on="PRODUCT_SET_SID", how="left")
        _extra_cols_cleaned = [c for c in _extra_cols if c in df_display.columns]
        if "IMAGE1_ZIP" in df_display.columns: _extra_cols_cleaned.append("IMAGE1_ZIP")
        df_display = df_display[list(dict.fromkeys(_extra_cols_cleaned + ["Is_Zip"]))]

        if title == "Category Max Price Exceeded" and "CAT_MAX_PRICE" in df_flagged_sids.columns:
            _cap_map = df_flagged_sids.set_index("ProductSetSid")["CAT_MAX_PRICE"].to_dict()
            sid_col = "PRODUCT_SET_SID" if "PRODUCT_SET_SID" in df_display.columns else "ProductSetSid"
            df_display["CAT_MAX_PRICE"] = df_display[sid_col].map(_cap_map)

        if title == "Wrong Category" and "Suggested_Category" in df_flagged_sids.columns:
            _sug_map = df_flagged_sids.set_index("ProductSetSid")["Suggested_Category"].to_dict()
            sid_col = "PRODUCT_SET_SID" if "PRODUCT_SET_SID" in df_display.columns else "ProductSetSid"
            df_display["AI Suggested Category"] = df_display[sid_col].map(_sug_map)

        _code_to_path = support_files.get("code_to_path", {})
        if _code_to_path and "CATEGORY_CODE" in df_display.columns:
            def _resolve_cat_exp(row):
                existing = str(row.get("CATEGORY", "")).strip()
                if existing and existing.lower() != "nan": return existing
                code = clean_category_code(row.get("CATEGORY_CODE", ""))
                return _code_to_path.get(code, code)
            df_display["CATEGORY"] = df_display.apply(_resolve_cat_exp, axis=1)
            df_display = df_display.drop(columns=["CATEGORY_CODE"])
        
        _final_cols = list(dict.fromkeys([c for c in current_display_cols if c in df_display.columns] + ["Is_Zip"]))
        df_display = df_display[_final_cols]
        st.session_state.display_df_cache[cache_key] = df_display
    else:
        df_display = st.session_state.display_df_cache[cache_key]

    _default_show_images = True if _is_dup_view else st.session_state.get("show_table_images", False)
    show_table_images = st.toggle("Show Image Previews", value=_default_show_images, key=f"tg_img_{title}")
    st.session_state.show_table_images = show_table_images

    c1, c2, c3 = st.columns([1, 1, 1], gap="large")
    with c1: search_term = st.text_input(_t("search_grid"), placeholder="Name, Brand...", icon=":material/search:", key=f"s_{title}")
    with c2:
        _seller_key = f"f_{title}"
        _seller_options = sorted(df_display["SELLER_NAME"].astype(str).unique())
        _seller_default = [s for s in st.session_state.get(f"_sf_{title}", []) if s in _seller_options]
        seller_filter = st.multiselect("Filter by Seller", _seller_options, default=_seller_default, key=_seller_key)
        st.session_state[f"_sf_{title}"] = seller_filter
    with c3:
        _cat_key = f"fc_{title}"
        _cat_options = sorted(df_display["CATEGORY"].dropna().astype(str).unique()) if "CATEGORY" in df_display.columns else []
        _cat_default = [c for c in st.session_state.get(f"_cf_{title}", []) if c in _cat_options]
        category_filter = st.multiselect("Filter by Category", _cat_options, default=_cat_default, key=_cat_key)
        st.session_state[f"_cf_{title}"] = category_filter

    df_view = df_display.copy()
    if search_term:
        _search_cols = [c for c in ["NAME", "BRAND", "SELLER_NAME"] if c in df_view.columns]
        if _search_cols:
            mask = df_view[_search_cols].apply(lambda col: col.astype(str).str.contains(search_term, case=False, na=False)).any(axis=1)
            df_view = df_view[mask]
    if seller_filter: df_view = df_view[df_view["SELLER_NAME"].isin(seller_filter)]
    if category_filter and "CATEGORY" in df_view.columns: df_view = df_view[df_view["CATEGORY"].astype(str).isin(category_filter)]
    if "CATEGORY" in df_view.columns: df_view = df_view.sort_values("CATEGORY", na_position="last")
    df_view = df_view.reset_index(drop=True)

    if "NAME" in df_view.columns: df_view["NAME"] = df_view["NAME"].apply(lambda t: re.sub("<[^<]+?>", "", t) if isinstance(t, str) else t)

    if img_col and img_col in df_view.columns:
        def get_img(row):
            sid = row.get("PRODUCT_SET_SID")
            name, brand = row.get("NAME", ""), row.get("BRAND", "")
            img_val = row.get(img_col, "")
            if pd.isna(img_val): img_val = ""
            zip_img = _get_image_from_zip(name, brand, img_val)
            if zip_img: return zip_img
            if "IMAGE1_ZIP" in row and pd.notna(row["IMAGE1_ZIP"]) and str(row["IMAGE1_ZIP"]).startswith("http"):
                return str(row["IMAGE1_ZIP"])
            if str(img_val).startswith("http"):
                return str(img_val).replace("http://", "https://", 1)
            return None
        if show_table_images:
            df_view["Image Preview"] = df_view.apply(get_img, axis=1)
            if _is_dup_view and "NAME" in df_view.columns:
                _cols = [c for c in df_view.columns if c != "Image Preview"]
                _name_pos = _cols.index("NAME")
                _cols.insert(_name_pos + 1, "Image Preview")
                df_view = df_view[_cols]

    if "GLOBAL_PRICE" in df_view.columns and "GLOBAL_SALE_PRICE" in df_view.columns:
        def _local_p(row):
            sp, rp = row.get("GLOBAL_SALE_PRICE"), row.get("GLOBAL_PRICE")
            val = sp if pd.notna(sp) and str(sp).strip() != "" else rp
            return format_local_price(val, country_validator.country)
        df_view.insert(
            df_view.columns.get_loc("GLOBAL_PRICE") + 1 if "GLOBAL_PRICE" in df_view.columns else len(df_view.columns),
            "Local Price", df_view.apply(_local_p, axis=1),
        )

    def style_rows(row):
        if row.get("Is_Zip"): return ["color: #ff4b4b; font-weight: 900;"] * len(row)
        return [""] * len(row)

    df_styled = df_view.style.apply(style_rows, axis=1)

    column_config = {}
    if "Image Preview" in df_view.columns:
        column_config["Image Preview"] = st.column_config.ImageColumn("Image Preview", help="Select a row's checkbox to see a larger preview below the table")

    _df_key = f"df_{title}_{st.session_state.get(f'df_ver_{title}', 0)}"
    sel_all_col, sel_clear_col, _sel_spacer = st.columns([1, 1, 3])
    with sel_all_col:
        if st.button(f"Select All ({len(df_view)})", key=f"selall_{title}", disabled=df_view.empty, help="Select every row currently shown below (after search/filters)"):
            st.session_state[_df_key] = {"selection": {"rows": list(range(len(df_view)))}}
    with sel_clear_col:
        if st.button("Clear Selection", key=f"selclear_{title}"):
            st.session_state[_df_key] = {"selection": {"rows": []}}

    event = st.dataframe(
        df_styled, hide_index=True, width='stretch', selection_mode="multi-row",
        column_config=column_config,
        on_select="rerun", key=_df_key,
    )

    raw_selected = list(event.selection.rows)
    selected_indices = [i for i in raw_selected if i < len(df_view)]
    has_selection = len(selected_indices) > 0
    _sel_color = JUMIA_COLORS["primary_orange"] if has_selection else "#aaa"
    st.markdown(
        f"<div style='display:inline-block;background:{_sel_color};color:#fff;padding:4px 14px;border-radius:9999px;font-size:13px;font-weight:700;margin-bottom:8px;'>{len(selected_indices)} / {len(df_view)} selected</div>",
        unsafe_allow_html=True,
    )

    if show_table_images and "Image Preview" in df_view.columns and has_selection:
        _PREVIEW_PER_ROW = 4
        _PREVIEW_MAX = 24
        _capped = len(selected_indices) > _PREVIEW_MAX
        _preview_rows = df_view.iloc[selected_indices[:_PREVIEW_MAX]]
        _shown = len(_preview_rows)
        _label = f"Image Preview ({_shown} of {len(selected_indices)} selected shown)" if _capped else f"Image Preview ({_shown} selected)"
        with st.expander(_label, expanded=True):
            if _capped:
                st.caption(f"Showing the first {_PREVIEW_MAX} — narrow your filters or selection to preview the rest.")
            _rows_list = list(_preview_rows.iterrows())
            for _chunk_start in range(0, len(_rows_list), _PREVIEW_PER_ROW):
                _chunk = _rows_list[_chunk_start:_chunk_start + _PREVIEW_PER_ROW]
                _pcols = st.columns(_PREVIEW_PER_ROW)
                for _pc, (_, _prow) in zip(_pcols, _chunk):
                    with _pc:
                        _psid = str(_prow.get("PRODUCT_SET_SID", "")).strip()
                        _pname = str(_prow.get("NAME", ""))[:70]
                        st.markdown(f"<div style='font-size:12px;font-weight:600;line-height:1.3;'>{_pname}</div><div style='font-size:11px;color:#888;margin-bottom:4px;'>SID: {_psid}</div>", unsafe_allow_html=True)
                        _img_url = _prow.get("Image Preview")
                        if _img_url: st.image(_img_url, width=140)
                        else: st.caption("No image")
                        _reason = _prefetched_reason_for_row(title, _prow, fallback="")
                        if not _reason:
                            _reason = _extract_specific_reason(_comment_map.get(_psid, ""))
                        if _reason and _reason.lower() not in ("nan", "none", "no reason provided"):
                            st.markdown(
                                f"<div style='background:#fef2f2;border:1px solid {JUMIA_COLORS['jumia_red']};color:{JUMIA_COLORS['jumia_red']};padding:6px 10px;border-radius:6px;font-size:11px;margin-top:6px;'>Warning: {_reason}</div>",
                                unsafe_allow_html=True,
                            )

    _fm = support_files["flags_mapping"]
    _reason_options = [
        "Wrong Category", "Restricted brands", "Suspected Fake product", "Seller Not approved to sell Refurb", "Product Warranty", "Seller Approve to sell books", "Seller Approved to Sell Perfume", "Counterfeit Sneakers", "Suspected counterfeit Jerseys", "Prohibited products", "Unnecessary words in NAME", "Single-word NAME", "Generic BRAND Issues", "Fashion brand issues", "BRAND name repeated in NAME", "Wrong Variation", "Generic branded products with genuine brands", "Missing COLOR", "Missing Weight/Volume", "Incomplete Smartphone Name", "Duplicate product", "Poor images", "Image Stretched", "Image Blurry", "Image Mismatch", "Image Infringing", "Image Too Many things displayed", "Perfume Tester", "NG - Gift Card Seller", "NG - Books Seller", "NG - TV Brand Seller", "NG - HP Toners Seller", "NG - Apple Seller", "NG - Xmas Tree Seller", "NG - Rice Brand Seller", "NG - Powerbank Capacity", "Discount too high", "Category Max Price Exceeded", "Suspicious Discount", "Color Mismatch", "FDA", "Category Check", "Warranty Check", "Color Check", "Variation Check", "Brand Image Check", "Title Language Check", "Image Quality Check", "Product Name Brand Name", "Other Reason (Custom)"
    ]

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("Approve Selected", key=f"approve_sel_{title}", type="primary", width='stretch', disabled=not has_selection):
            sids_to_process = df_view.iloc[selected_indices]["PRODUCT_SET_SID"].tolist()
            subset = data[data["PRODUCT_SET_SID"].isin(sids_to_process)]
            _clear_flag_df_selection(title)
            bulk_approve_dialog(sids_to_process, title, subset, data_has_warranty_cols_check, support_files, country_validator, validation_runner)

    with btn_col2:
        pop_ver = st.session_state.get(f"pop_ver_{title}", 0)
        with st.popover("Reject Selected As...", width='stretch', disabled=not has_selection, key=f"popover_rej_{title}_{pop_ver}"):
            chosen_reason = st.selectbox("Reason", _reason_options, key=f"rej_reason_dd_{title}", label_visibility="collapsed")
            _cmt_lang = "fr" if st.session_state.get("selected_country") == "Morocco" else "en"

            if chosen_reason == "Other Reason (Custom)":
                custom_comment = st.text_area("Custom comment", placeholder="Type your rejection reason here...", key=f"custom_comment_{title}", height=80)
                if st.button("Apply", key=f"apply_custom_{title}", type="primary", width='stretch', disabled=not has_selection):
                    to_reject = df_view.iloc[selected_indices]["PRODUCT_SET_SID"].tolist()
                    final_comment = custom_comment.strip() if custom_comment.strip() else "Other Reason"
                    apply_status_change(to_reject, status="Rejected", reason="1000007 - Other Reason", comment=final_comment, flag="Other Reason (Custom)", is_manual=True, is_zip=False)
                    st.session_state[f"exp_{title}"] = True
                    _clear_flag_df_selection(title)
                    st.session_state[f"pop_ver_{title}"] = pop_ver + 1
                    st.toast(f"{len(to_reject)} items rejected with custom reason.")
                    st.rerun(scope="fragment")
            else:
                _rinfo = _fm.get(chosen_reason, {"reason": "1000007 - Other Reason", "en": chosen_reason})
                _rcode = _rinfo["reason"]
                _rcmt = _rinfo.get(_cmt_lang, _rinfo.get("en"))
                st.info(f"**Seller message:** {_rcmt}", icon=":material/chat:")
                if st.button("Apply", key=f"apply_dd_{title}", type="primary", width='stretch', disabled=not has_selection):
                    to_reject = df_view.iloc[selected_indices]["PRODUCT_SET_SID"].tolist()
                    apply_status_change(to_reject, status="Rejected", reason=_rcode, comment=_rcmt, flag=chosen_reason, is_manual=True, is_zip=False)
                    
                    if chosen_reason == "Wrong Category" and title != "Wrong Category" and _CAT_MATCHER_AVAILABLE:
                        try:
                            engine = get_engine()
                            _cats = support_files.get("categories_names_list", [])
                            if engine is not None and _cats:
                                if not engine._tfidf_built: engine.build_tfidf_index(_cats)
                                learned = 0
                                for sid in to_reject:
                                    prod_row = data[data["PRODUCT_SET_SID"].astype(str).str.strip() == str(sid)]
                                    if prod_row.empty: continue
                                    name = str(prod_row.iloc[0].get("NAME", "")).strip()
                                    if not name: continue
                                    engine.set_compiled_rules(st.session_state.get("compiled_json_rules", {}))
                                    predicted = engine.get_category_with_boost(name)
                                    if predicted and predicted.lower() not in ("nan", "none", "uncategorized", ""):
                                        engine.apply_learned_correction(name, predicted, auto_save=False)
                                        learned += 1
                                if learned:
                                    import threading as _threading
                                    _threading.Thread(target=engine.save_learning_db, daemon=True).start()
                        except Exception as _le:
                            logger.warning("Wrong Category manual rejection learning failed: %s", _le)

                    st.session_state[f"exp_{title}"] = True
                    _clear_flag_df_selection(title)
                    st.session_state[f"pop_ver_{title}"] = pop_ver + 1
                    st.toast(f"Successfully rejected {len(to_reject)} items as '{chosen_reason}'.")
                    st.rerun(scope="fragment")

def build_fast_grid_html(
    page_data, flags_mapping, country, page_warnings, rejected_state, cols_per_row,
    poor_img_sids=None, prefetch_urls=None, scroll_to_top=False, show_images=True, seller_trust=None, support_files=None,
    initial_sort="", initial_filter="", all_matching_sids=None, initial_search="", initial_selections_json="{}"
):
    if seller_trust is None: seller_trust = {}
    if support_files is None: support_files = {}
    
    from translations import get_translation
    lang = "fr" if country == "Morocco" else "en"

    def _t(key): return get_translation(lang, key)

    O = JUMIA_COLORS["primary_orange"]
    G = JUMIA_COLORS["success_green"]
    R = JUMIA_COLORS["jumia_red"]
    def _js_json(v): return orjson.dumps(v).decode("utf-8").replace("</", "<\\/")

    committed_json = _js_json(rejected_state)
    poor_img_sids_json = _js_json(list(poor_img_sids or []))
    prefetch_json = _js_json(prefetch_urls or [])
    all_matching_sids_json = _js_json(list(all_matching_sids or []))
    html_dir = "rtl" if st.session_state.get("ui_lang") == "ar" else "ltr"

    labels_dict = {
        "poor_img": _t("poor_img"), "wrong_cat": _t("wrong_cat"), "fake_prod": _t("fake_prod"), "restr_brand": _t("restr_brand"),
        "wrong_brand": _t("wrong_brand"), "prohibited": _t("prohibited"), "missing_color": _t("missing_color"),
        "more_options": _t("more_options"), "undo": _t("undo"), "approve": _t("approve_btn"), "clear_sel": _t("clear_sel"),
        "items_pending": _t("items_pending"), "batch_reject": _t("batch_reject"), "select_all": _t("select_all"),
        "deselect_all": _t("deselect_all"), "rejected": str(_t("rejected") or "REJECTED").upper(),
    }
    labels_json = _js_json(labels_dict)

    _PLACEHOLDER_SVG = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='180' viewBox='0 0 300 180'>"
        "<defs><linearGradient id='g' x1='0%' y1='0%' x2='100%' y2='100%'><stop offset='0%' stop-color='%23FFF8F2'/><stop offset='100%' stop-color='%23FFEFE5'/></linearGradient></defs>"
        "<rect width='300' height='180' rx='12' fill='url(%23g)'/>"
        "<text x='150' y='80' text-anchor='middle' font-family='sans-serif' font-size='34' font-weight='800' fill='%23FF8800' letter-spacing='-1'>JUMIA</text>"
        "<text x='150' y='110' text-anchor='middle' font-family='sans-serif' font-size='14' font-weight='600' fill='%23FF8800' opacity='0.7'>Loading...</text>"
        "</svg>"
    )

    _zip_img_cache: dict = {}
    cards_data = []
    for row in page_data.to_dict("records"):
        sid = str(row["PRODUCT_SET_SID"])
        img_url = str(row.get("MAIN_IMAGE", "")).strip()
        if img_url.startswith("http"): 
            img_url = img_url.replace("http://", "https://", 1)
            if ".jumia.is" in img_url:
                img_url = img_url.replace("fit-in/680x680", "fit-in/200x200").replace("fit-in/500x500", "fit-in/200x200")
        elif img_url:
            name, brand = str(row.get("NAME", "")).strip(), str(row.get("BRAND", "")).strip()
            _zip_cache_key = (name, brand, img_url)
            if _zip_cache_key not in _zip_img_cache: _zip_img_cache[_zip_cache_key] = _get_image_from_zip(name, brand, img_url)
            img_data = _zip_img_cache[_zip_cache_key]
            if img_data: img_url = img_data
            else: img_url = ""

        if (not img_url or img_url == "") and "IMAGE1_ZIP" in row:
            _fallback = str(row.get("IMAGE1_ZIP", "")).strip()
            if _fallback.startswith("http"): img_url = _fallback.replace("http://", "https://", 1)

        def _parse_price(v):
            if v is None: return None
            s = str(v).strip()
            if s.lower() in ("", "nan", "none", "null", "0", "0.0"): return None
            try:
                f = float(s)
                return f if f > 0 else None
            except (ValueError, TypeError): return None

        sale_p = _parse_price(row.get("GLOBAL_SALE_PRICE"))
        reg_p = _parse_price(row.get("GLOBAL_PRICE"))
        usd_val = sale_p if sale_p is not None else reg_p
        price_str = format_local_price(usd_val, st.session_state.get("selected_country", "Kenya")) if usd_val is not None else ""

        color_val = str(row.get("COLOR", "")).strip()
        if color_val.lower() in ("nan", "none", "null"): color_val = ""

        color_ai = str(row.get("Color_AI_Normalized", "")).strip()
        if color_ai.lower() in ("nan", "none", "null", ""): color_ai = ""
        color_mismatch = ""
        if color_ai and color_val:
            _ai_n, _dec_n = color_ai.lower().replace(" ", ""), color_val.lower().replace(" ", "")
            if _ai_n != _dec_n and _ai_n not in _dec_n and _dec_n not in _ai_n: color_mismatch = f"AI detected '{color_ai}' but declared '{color_val}'"
        elif color_ai and not color_val: color_mismatch = f"AI detected color '{color_ai}' but no color declared"

        dup_raw = str(row.get("Duplicate_Flag", "")).strip()
        is_duplicate = dup_raw.lower() not in ("", "nan", "none", "false")

        mr_raw = str(row.get("Manual_Review", "")).strip().lower()
        is_manual_review = mr_raw in ("true", "1", "yes")

        cat_reason = str(row.get("Category_Check_Rejection_Reason", "")).strip()
        if cat_reason.lower() in ("nan", "none", "rejected", ""): cat_reason = ""
        suggested_cats_raw = str(row.get("Suggested_Categories", "")).strip()
        suggested_cat = ""
        if suggested_cats_raw and suggested_cats_raw.lower() not in ("nan", "none", ""):
            first_pipe = suggested_cats_raw.split("|")[0]
            suggested_cat = re.sub(r"\s*\(\d+%\)\s*$", "", first_pipe).strip()

        ai_caption = str(row.get("AI_Product_Caption", "")).strip()
        if ai_caption.lower() in ("nan", "none", ""): ai_caption = ""
        
        b_detect_1 = str(row.get("Brand_Detected_On_Product", "")).strip()
        b_detect_2 = str(row.get("brand_detected_on_product", "")).strip()
        b_detect_3 = str(row.get("Detected_Brand", "")).strip()
        b_detect = b_detect_1 if b_detect_1.lower() not in ("nan","none","") else (b_detect_2 if b_detect_2.lower() not in ("nan","none","") else (b_detect_3 if b_detect_3.lower() not in ("nan","none","") else ""))

        cards_data.append({
            "sid": sid, "img": img_url if show_images else _PLACEHOLDER_SVG, "name": str(row.get("NAME", "")),
            "brand": str(row.get("BRAND", "Unknown Brand")), "cat": str(row.get("CATEGORY", "Unknown Category")),
            "seller": str(row.get("SELLER_NAME", "Unknown Seller")), "color": color_val, "brand_detected": b_detect,
            "warnings": page_warnings.get(sid, []), "price": price_str, "data_name": str(row.get("NAME", "")).replace('"', "&quot;"),
            "data_brand": str(row.get("BRAND", "")).replace('"', "&quot;"), "data_sid": sid, "data_cat": str(row.get("CATEGORY", "")).replace('"', "&quot;"),
            "is_duplicate": is_duplicate, "is_manual_review": is_manual_review, "color_mismatch": color_mismatch,
            "cat_reason": cat_reason, "suggested_cat": suggested_cat, "ai_caption": ai_caption, "is_in_zip": bool(row.get("Is_Zip", False)),
        })

    cards_json = orjson.dumps(cards_data).decode("utf-8").replace("</", "<\\/")
    scroll_js = "sessionStorage.removeItem('__inner_iframe_scroll__'); window.scrollTo(0, 0);" if scroll_to_top else "var savedInnerScroll = sessionStorage.getItem('__inner_iframe_scroll__'); if (savedInnerScroll) { setTimeout(function() { window.scrollTo({top: parseInt(savedInnerScroll, 10), behavior: 'instant'}); }, 50); }"

    initial_search_safe = initial_search.replace('"', '&quot;')

    return f"""<!DOCTYPE html>
<html dir="{html_dir}">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<link rel="preconnect" href="https://ke.jumia.is" crossorigin>
<link rel="preconnect" href="https://ng.jumia.is" crossorigin>
<link rel="preconnect" href="https://ma.jumia.is" crossorigin>
<link rel="preconnect" href="https://gh.jumia.is" crossorigin>
<link rel="preconnect" href="https://ug.jumia.is" crossorigin>
<style>
  :root {{ --bg: #f9fafb; --card: #ffffff; --text: #111827; --border: #e5e7eb; --accent: {O}; }}
  *{{box-sizing:border-box;margin:0;padding:0;font-family:sans-serif;}}
  body{{background:var(--bg);color:var(--text);padding:8px;overflow-x:hidden;width:100%;transition:background .2s, color .2s;}}
  .ctrl-bar{{position:-webkit-sticky;position:sticky;top:0;z-index:99999;display:flex;flex-direction:column;align-items:stretch;gap:0;padding:8px 12px;background:var(--card);backdrop-filter:blur(8px);border-bottom:2px solid var(--accent);border-radius:4px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.15);}}
  #grid-search {{ flex: 1; min-width: 200px; padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border); font-size: 13px; outline: none; background: var(--bg); color: var(--text); }}
  #clear-search-btn {{ padding: 6px 12px; background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; font-weight: 600; white-space: nowrap; }}
  #dark-toggle {{ padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600; }}
  #dark-toggle:hover {{ background: #f3f4f6; }}
  .bottom-bar {{position: relative; border-bottom: none; border-top: 2px solid {O}; margin-top: 16px; margin-bottom: 0; z-index: 10; box-shadow: 0 -4px 16px rgba(0,0,0,0.05);}}
  .sel-count{{font-weight:700;color:{O};font-size:13px;min-width:80px;}}
  .reason-sel{{flex:1;min-width:160px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:12px;background:#fff;cursor:pointer;}}
  .batch-btn{{padding:7px 14px;background:{O};color:#fff;border:none;border-radius:4px;font-weight:700;font-size:12px;cursor:pointer;}}
  .batch-btn:hover{{opacity:.88;}}
  .desel-btn{{padding:7px 12px;background:#fff;color:#555;border:1px solid #ccc;border-radius:4px;font-size:12px;cursor:pointer;}}
  .desel-btn:hover{{background:#f5f5f5;}}
  .top-btn {{margin-left: auto; background: #313133; color: white; border-color: #313133; font-weight: bold;}}
  .top-btn:hover {{background: #000; color: white;}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;width:100%;}}
  .card{{content-visibility: auto; contain-intrinsic-size: 360px; border:2px solid var(--border);border-radius:8px;padding:10px;background:var(--card);position:relative;transition:border-color .15s,box-shadow .15s,transform .2s;z-index:1;min-width:0;word-wrap:break-word;display:flex;flex-direction:column;min-height:360px;outline:none;}}
  .card:focus-visible, .card.keyboard-focused {{ outline: none !important; border-color: var(--accent) !important; box-shadow: 0 0 0 4px rgba(246, 139, 30, 0.4), 0 10px 25px rgba(0, 0, 0, 0.08) !important; transform: translateY(-4px) scale(1.01); z-index: 10; }}
  .card.selected{{border-color:{O};box-shadow:0 0 0 5px rgba(255,136,0,.25), 0 8px 24px rgba(0,0,0,0.08);background:rgba(255,136,0,.02);transform:scale(0.99);}}
  .card.staged-rej{{border-color:{R};box-shadow:0 0 0 4px rgba(231,60,23,.3);background:rgba(231,60,23,.04);}}
  .card.committed-rej{{border-color:#bbb;opacity:.6;}}
  .card-img-wrap{{position:relative;cursor:pointer;border-radius:8px;background:#fff;display:flex;align-items:center;justify-content:center;height:180px;overflow:hidden; border:1px solid #111;flex-shrink:0;}}
  .card-img-wrap::before{{content:'';position:absolute;inset:0;background:linear-gradient(90deg,#FFF8F2 25%,#FFEFE5 50%,#FFF8F2 75%);background-size:200% 100%;animation:shimmer 1.4s infinite;z-index:1;}}
  .card-img-wrap.img-loaded::before{{display:none;}}
  @keyframes shimmer{{0%{{background-position:200% 0}}100%{{background-position:-200% 0}}}}
  .card-img-placeholder{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:1;}}
  .card-img{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:2;opacity:0;transition:opacity .4s ease;}}
  .card-img.img-loaded{{opacity:1;}}
  .card.committed-rej .card-img{{filter:grayscale(80%);}}
  .warn-wrap{{position:absolute;top:8px;right:8px;display:flex;flex-direction:column;gap:4px;z-index:10;pointer-events:none;}}
  .warn-badge{{font-size:9px;font-weight:800;padding:3px 8px;border-radius:9999px;box-shadow:0 2px 6px rgba(0,0,0,0.08);background:#d97706;color:#fff;display:inline-block;white-space:nowrap;text-transform:uppercase;letter-spacing:0.5px;}}
  .warn-badge.critical{{background:#dc2626;}}
  .warn-badge.duplicate{{background:#7c3aed;}}
  .warn-badge.manual{{background:#0284c7;}}
  .warn-badge.mismatch{{background:#b45309;}}
  .price-badge{{position:absolute;top:8px;left:8px;background:rgba(246,139,30,0.95);color:#fff;font-size:10px;font-weight:800;padding:3px 8px;border-radius:9999px;z-index:10;pointer-events:none;box-shadow:0 2px 6px rgba(0,0,0,.2);}}
  .meta{{font-size:11px;margin-top:8px;line-height:1.4;flex-grow:1;display:flex;flex-direction:column;}}
  .meta .nm{{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:help;}}
  .meta .br{{color:{O};font-weight:700;margin:2px 0;}}
  .meta .ct{{color:#666;font-size:10px;word-break:break-word;}}
  .meta .sl{{color:#999;font-size:9px;margin-top:4px;border-top:1px dashed #eee;padding-top:4px;cursor:help;display:flex;justify-content:space-between;align-items:center;}}
  .meta .co{{color:#555;font-size:10px;margin-top:4px;background:#f0f0f0;padding:3px 5px;border-radius:4px;display:inline-block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;font-weight:600;}}
  .acts{{display:flex;gap:4px;margin-top:auto;padding-top:8px;}}
  .act-btn{{flex:1;padding:6px;font-size:11px;border:none;border-radius:4px;cursor:pointer;font-weight:700;color:#fff;background:{O};}}
  .act-more{{flex:1;font-size:11px;border:1px solid #ccc;border-radius:4px;outline:none;cursor:pointer;background:#fff;}}
  .zoom-btn{{position:absolute;bottom:6px;right:6px;width:22px;height:22px;background:rgba(0,0,0,0.4);color:#fff;border-radius:4px;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:25;border:none;transition:background .2s;}}
  .zoom-btn:hover{{background:rgba(0,0,0,0.7);}}
  .zoom-btn svg{{width:12px;height:12px;flex-shrink:0;}}
  .tick{{position:absolute;bottom:6px;left:6px;width:22px;height:22px;border-radius:50%;background:rgba(0,0,0,.18);display:flex;align-items:center;justify-content:center;color:transparent;font-size:13px;font-weight:900;pointer-events:none;z-index:10;}}
  .card.selected .tick{{background:{O};color:#fff;}}
  .card.committed-rej.selected .tick{{z-index:25;background:{O};color:#fff;}}
  .card.committed-rej.selected{{box-shadow:0 0 0 4px {O},0 0 0 8px rgba(255,136,0,.25)!important;}}
  .rej-overlay{{display:none;position:absolute;inset:0;background:rgba(255,255,255,.90);border-radius:8px;flex-direction:column;align-items:center;justify-content:center;z-index:20;gap:8px;padding:12px;text-align:center;}}
  .card.committed-rej .rej-overlay{{display:flex;}}
  .card.committed-rej.poor-img-rej .rej-overlay{{background:rgba(0,0,0,.45);backdrop-filter:blur(1px);}}
  .card.committed-rej.poor-img-rej{{border-color:{R};opacity:1;}}
  .card.committed-rej.poor-img-rej .card-img{{filter:none;}}
  .card.committed-rej.poor-img-rej .rej-badge{{background:rgba(231,60,23,.9);}}
  .card.committed-rej.poor-img-rej .rej-label{{color:#fff;}}
  .card.committed-rej.poor-img-rej .undo-btn{{background:#fff;color:{R};}}
  .card.committed-rej.poor-img-rej .undo-btn:hover{{background:#f0f0f0;}}
  .card.staged-rej .rej-overlay.staged{{display:flex; background:rgba(211,47,47,0.85);}}
  .card.staged-rej .rej-badge.pending{{background:transparent; color:#fff; font-size:22px; font-weight:900; padding:0; letter-spacing:1px;}}
  .card.staged-rej .rej-label{{color:#fff; font-size:13px; font-weight:600; line-height:1.2; max-width:140px;}}
  .card.committed-rej .rej-badge{{background:{R};color:#fff;padding:6px 12px;border-radius:6px;font-size:15px;font-weight:800;letter-spacing:0.5px;}}
  .card.committed-rej .rej-label{{font-size:12px;color:{R};font-weight:700;max-width:130px;}}
  .undo-btn{{margin-top:8px;padding:6px 14px;background:#313133;color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:bold;cursor:pointer;}}
  .undo-btn:hover{{background:#000;}}
  .card.staged-rej .undo-btn{{background:#fff; color:#D32F2F; box-shadow:0 2px 6px rgba(0,0,0,0.2);}}
  .card.staged-rej .undo-btn:hover{{background:#f0f0f0;}}
  .card.committed-rej.brand-image-rej .rej-badge {{ background: #2E7D32 !important; }}
  .card.committed-rej.brand-image-rej .rej-label {{ color: #2E7D32 !important; }}
  .card.committed-rej.brand-image-rej .rej-overlay {{ background: rgba(232, 245, 233, 0.6) !important; }}
  .card.zip-product {{ border-left: 3px solid #3b82f6 !important; }}
  .hlt {{ background: #fee2e2; color: #b91c1c; font-weight: 800; border-radius: 2px; padding: 0 2px; }}
  .trust-badge {{ position: absolute; top: 10px; left: 10px; background: #ef4444; color: #fff; font-size: 10px; font-weight: 800; padding: 4px 8px; border-radius: 6px; z-index: 100; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4); cursor: pointer; transition: transform 0.2s; }}
  .trust-badge:hover {{ transform: scale(1.1); background: #dc2626; }}
  .card.undo-processing {{ pointer-events: none; }}
  .card.undo-processing::after {{ content: ''; position: absolute; inset: 0; border-radius: 8px; background: rgba(255,255,255,0.55); backdrop-filter: blur(2px); z-index: 30; animation: undoPulse 0.5s ease-in-out infinite alternate; }}
  @keyframes undoPulse {{ from {{ opacity: 0.4; }} to {{ opacity: 0.85; }} }}
  #zoom-tooltip  .ctrl-bar {{ display: flex; align-items: center; gap: 8px; padding: 10px 16px; background: rgba(255, 255, 255, 0.75); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid rgba(246, 139, 30, 0.2); position: sticky; top: 0; z-index: 100; }}
  #action-drawer {{
    max-height: 0;
    opacity: 0;
    overflow: hidden;
    transition: max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease-out, padding 0.3s, margin 0.3s;
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 0;
    margin: 0;
  }}
  #bottom-action-drawer {{
    max-height: 0;
    opacity: 0;
    overflow: hidden;
    transition: max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease-out, padding 0.3s, margin 0.3s;
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    background: var(--card);
  }}
  #bottom-action-drawer.expanded {{
    max-height: 60px;
    opacity: 1;
    padding: 12px 16px;
    margin-top: 16px;
    border: 2px solid var(--accent);
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.1);
  }}
  #action-drawer.expanded {{
    max-height: 60px;
    opacity: 1;
    padding: 8px 0 0 0;
    margin-top: 8px;
    border-top: 1px dashed var(--border);
  }}
  .drawer-left {{
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    font-weight: 600;
  }}
  .drawer-right {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  #prefetch-status {{
    margin-top: 12px;
    margin-bottom: 24px;
    font-size: 11px;
    color: #666;
  }}
  @keyframes shimmer {{ 0% {{ background-position: -1000px 0; }} 100% {{ background-position: 1000px 0; }} }}
  .skeleton {{ background: #f6f7f8; background-image: linear-gradient(to right, #f6f7f8 0%, #edeef1 20%, #f6f7f8 40%, #f6f7f8 100%); background-repeat: no-repeat; background-size: 2000px 100%; animation: shimmer 2s infinite linear; }}
  .card {{ background: #fff; border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; border: 1px solid #eee; position: relative; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
  .card:hover {{ transform: translateY(-6px) scale(1.01); box-shadow: 0 12px 30px rgba(0,0,0,0.12); z-index: 10; }}
  .card:focus-visible, .card.keyboard-focused {{ outline: none !important; border-color: var(--accent) !important; box-shadow: 0 0 0 4px rgba(246, 139, 30, 0.4), 0 12px 30px rgba(0, 0, 0, 0.12) !important; transform: translateY(-6px) scale(1.01); z-index: 10; }}
  #zoom-tooltip {{ display: none; position: fixed; bottom: 90px; right: 24px; z-index: 100000; background: #fff; padding: 12px; border-radius: 12px; box-shadow: 0 12px 48px rgba(0,0,0,0.25); border: 2px solid var(--accent); width: 320px; height: 320px; transition: opacity 0.2s ease, transform 0.2s ease; }}
  #tooltip-img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
  .tooltip-close {{ position: absolute; top: -12px; right: -12px; background: #333; color: #fff; border-radius: 50%; width: 28px; height: 28px; border: 2px solid #fff; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.3); }}
  .tooltip-close:hover {{ background: #000; }}
  #custom-reason-panel {{ display: none; position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: #fff; border: 2px solid {O}; border-radius: 8px; padding: 16px 20px; z-index: 999999; box-shadow: 0 8px 32px rgba(0,0,0,0.25); min-width: 340px; max-width: 480px; }}
  #custom-reason-panel h4 {{ margin: 0 0 10px 0; font-size: 13px; color: #333; }}
  #custom-reason-input {{ width: 100%; padding: 8px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; margin-bottom: 10px; box-sizing: border-box; }}
  #custom-reason-input:focus {{ outline: 2px solid {O}; border-color: {O}; }}
  .custom-panel-btns {{ display: flex; gap: 8px; }}
  .custom-panel-btns button {{ flex: 1; padding: 7px; border-radius: 4px; font-size: 12px; font-weight: 700; cursor: pointer; border: none; }}
  .custom-panel-confirm {{ background: {O}; color: #fff; }}
  .custom-panel-confirm:hover {{ opacity: 0.88; }}
  .custom-panel-cancel {{ background: #e0e0e0; color: #333; }}
  .custom-panel-cancel:hover {{ background: #ccc; }}
</style>
</head>
<body>

<div id="custom-reason-panel">
  <h4>Enter custom rejection reason</h4>
  <input id="custom-reason-input" type="text" placeholder="Type your reason here…" maxlength="200">
  <div class="custom-panel-btns">
    <button class="custom-panel-confirm" onclick="confirmCustomReason()">Apply</button>
    <button class="custom-panel-cancel" onclick="cancelCustomReason()">Cancel</button>
  </div>
</div>

<div class="ctrl-bar">
  <div class="ctrl-row" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; width:100%;">
    <div style="display:flex; gap:8px; margin-right:12px; align-items:center; flex:1; min-width:200px;">
        <input id="grid-search" type="search" placeholder="Search by name, brand, SID, category, or seller..." value="{initial_search_safe}">
        <button id="clear-search-btn" onclick="window.clearSearch()">Clear</button>
    </div>
    <div id="grid-count" style="font-size:11px; color:var(--text); opacity:0.7; margin-right:10px;">{len(page_data)} products</div>
    <button id="dark-toggle" onclick="toggleDark()">Dark</button>
    <button class="desel-btn" onclick="window.doSelectAll()">{labels_dict["select_all"]}</button>
    <button class="batch-btn top-btn" onclick="window.scrollTo(0, document.body.scrollHeight)">{_t("go_bottom")}</button>
    <select class="reason-sel sort-sel" id="sort-sel-top" onchange="applySort(this.value)" style="max-width:170px;" title="Sort by issue">
      <option value="">Sort by issue</option>
      <option value="most_flagged">Most Flagged First</option>
      <option value="no_issue">No Issues First</option>
      <option disabled>── Image ──</option>
      <option value="low_res">Low Resolution</option>
      <option value="tall">Tall (Screenshot?)</option>
      <option value="wide">Wide Aspect</option>
      <option value="broken">Broken Image</option>
      <option disabled>── QC Flags ──</option>
      <option value="Wrong Category">Wrong Category</option>
      <option value="Restricted brands">Restricted brands</option>
      <option value="Suspected Fake product">Suspected Fake</option>
      <option value="Missing COLOR">Missing Color</option>
      <option value="Product Warranty">Warranty Issues</option>
      <option value="Duplicate product">Duplicates</option>
      <option disabled>── Prefetch Flags ──</option>
      <option value="Category Check">Category Check</option>
      <option value="Warranty Check">Warranty Check</option>
      <option value="FDA">FDA</option>
      <option value="Color Check">Color Check</option>
      <option value="Variation Check">Variation Check</option>
      <option value="Brand Image Check">Brand Image Check</option>
      <option value="Title Language Check">Title Language Check</option>
      <option value="Image Quality Check">Image Quality Check</option>
      <option value="Product Name Brand Name">Name/Brand Check</option>
    </select>
    <select class="reason-sel sort-sel" id="filter-sel-top" onchange="applyFilter(this.value)" style="max-width:180px;" title="Filter to show only cards matching a flag">
      <option value="">Filter by flag</option>
      <option value="brand_ocr">Brand Image OCR</option>
      <option value="duplicates">Duplicates</option>
      <option value="manual_review">Manual Review</option>
      <option value="color_mismatch">Color Mismatch</option>
      <option value="committed">All Rejected</option>
      <option value="no_flags">Clean (no flags)</option>
      <option disabled>── QC Flags ──</option>
      <option value="Wrong Category">Wrong Category</option>
      <option value="Restricted brands">Restricted brands</option>
      <option value="Suspected Fake product">Suspected Fake</option>
      <option value="Missing COLOR">Missing Color</option>
      <option value="Product Warranty">Warranty Issues</option>
      <option value="Duplicate product">Duplicates</option>
      <option value="BRAND name repeated in NAME">Brand in Name</option>
      <option value="Unnecessary words">Unnecessary Words</option>
      <option value="Prohibited Words">Prohibited Words</option>
      <option disabled>── Prefetch Flags ──</option>
      <option value="Category Check">Category Check</option>
      <option value="Warranty Check">Warranty Check</option>
      <option value="FDA">FDA</option>
      <option value="Color Check">Color Check</option>
      <option value="Variation Check">Variation Check</option>
      <option value="Brand Image Check">Brand Image Check</option>
      <option value="Title Language Check">Title Language Check</option>
      <option value="Image Quality Check">Image Quality Check</option>
      <option value="Product Name Brand Name">Name/Brand Check</option>
      <option disabled>── Image Flags ──</option>
      <option value="Poor images">Poor Image</option>
      <option value="Low Resolution">Low Resolution</option>
      <option value="Tall (Screenshot?)">Tall/Screenshot</option>
      <option value="Wide Aspect">Wide Aspect</option>
      <option value="Broken Image">Broken Image</option>
    </select>
  </div>
  
  <div id="action-drawer">
    <div class="drawer-left">
      <span class="sel-count-text" style="font-weight:700; color:var(--accent); font-size:13px;">0 {labels_dict["items_pending"]}</span>
      <span style="opacity:0.7;">selected for batch action:</span>
    </div>
    <div class="drawer-right">
      <select class="reason-sel" id="batch-reason-drawer">
        <option value="REJECT_POOR_IMAGE">{labels_dict["poor_img"]}</option>
        <option value="REJECT_IMG_STRETCHED">Image Stretched</option>
        <option value="REJECT_IMG_BLURRY">Image Blurry</option>
        <option value="REJECT_IMG_MISMATCH">Image Mismatch</option>
        <option value="REJECT_IMG_INFRINGING">Image Infringing</option>
        <option value="REJECT_IMG_TOO_MANY">Image Too Many Things</option>
        <option value="REJECT_WRONG_CAT">{labels_dict["wrong_cat"]}</option>
        <option value="REJECT_FAKE">{labels_dict["fake_prod"]}</option>
        <option value="REJECT_BRAND">{labels_dict["restr_brand"]}</option>
        <option value="REJECT_WRONG_BRAND">{labels_dict["wrong_brand"]}</option>
        <option value="REJECT_PROHIBITED">{labels_dict["prohibited"]}</option>
        <option value="REJECT_COLOR">{labels_dict["missing_color"]}</option>
        <option value="REJECT_DUPLICATE">Duplicate product</option>
        <option value="REJECT_WEIGHT_VOL">Missing Weight/Volume</option>
        <option value="REJECT_BRAND_REPEAT">BRAND name repeated in NAME</option>
        <option value="REJECT_FAKE_PERFUME">Suspected Fake Perfume</option>
        <option value="OTHER_CUSTOM">Other Reason (Custom)</option>
      </select>
      <button class="batch-btn" onclick="doBatchReject('drawer')">{labels_dict["batch_reject"]}</button>
      <button class="batch-btn" onclick="window.batchApprove()" style="background:#16a34a;">Approve All</button>
      <button class="desel-btn" onclick="doBatchUndo()">{labels_dict["undo"]}</button>
      <button class="desel-btn" onclick="doDeselAll()">{labels_dict["deselect_all"]}</button>
    </div>
  </div>
</div>

<div id="shortcut-help" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5); z-index:9999999;align-items:center;justify-content:center;">
  <div style="background:var(--card);color:var(--text);border-radius:16px;padding:32px;min-width:280px;box-shadow:0 20px 50px rgba(0,0,0,0.3);">
    <h3 style="margin:0 0 16px">Keyboard Shortcuts</h3>
    <table style="border-collapse:collapse;width:100%;font-size:14px;">
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">j</kbd> / <kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">→</kbd></td><td style="padding-left:10px;">Next card</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">k</kbd> / <kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">←</kbd></td><td style="padding-left:10px;">Prev card</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">a</kbd></td><td style="padding-left:10px;">Approve focused</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">r</kbd></td><td style="padding-left:10px;">Reject focused</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">?</kbd></td><td style="padding-left:10px;">Toggle help</td></tr>
    </table>
    <button onclick="document.getElementById('shortcut-help').style.display='none'" style="margin-top:20px;width:100%;padding:10px;border-radius:8px; background:var(--accent);color:#fff;border:none;cursor:pointer;font-weight:700;">Got it!</button>
  </div>
</div>

<div class="grid" id="card-grid"></div>

<div id="bottom-action-drawer">
  <div class="drawer-left">
    <span class="sel-count-text" style="font-weight:700; color:var(--accent); font-size:13px;">0 {labels_dict["items_pending"]}</span>
    <span style="opacity:0.7;">selected for batch action:</span>
  </div>
  <div class="drawer-right">
    <select class="reason-sel" id="batch-reason-bottom">
      <option value="REJECT_POOR_IMAGE">{labels_dict["poor_img"]}</option>
      <option value="REJECT_IMG_STRETCHED">Image Stretched</option>
      <option value="REJECT_IMG_BLURRY">Image Blurry</option>
      <option value="REJECT_IMG_MISMATCH">Image Mismatch</option>
      <option value="REJECT_IMG_INFRINGING">Image Infringing</option>
      <option value="REJECT_IMG_TOO_MANY">Image Too Many Things</option>
      <option value="REJECT_WRONG_CAT">{labels_dict["wrong_cat"]}</option>
      <option value="REJECT_FAKE">{labels_dict["fake_prod"]}</option>
      <option value="REJECT_BRAND">{labels_dict["restr_brand"]}</option>
      <option value="REJECT_WRONG_BRAND">{labels_dict["wrong_brand"]}</option>
      <option value="REJECT_PROHIBITED">{labels_dict["prohibited"]}</option>
      <option value="REJECT_COLOR">{labels_dict["missing_color"]}</option>
      <option value="REJECT_DUPLICATE">Duplicate product</option>
      <option value="REJECT_WEIGHT_VOL">Missing Weight/Volume</option>
      <option value="REJECT_BRAND_REPEAT">BRAND name repeated in NAME</option>
      <option value="REJECT_FAKE_PERFUME">Suspected Fake Perfume</option>
      <option value="OTHER_CUSTOM">Other Reason (Custom)</option>
    </select>
    <button class="batch-btn" onclick="doBatchReject('bottom')">{labels_dict["batch_reject"]}</button>
    <button class="batch-btn" onclick="window.batchApprove()" style="background:#16a34a;">Approve All</button>
    <button class="desel-btn" onclick="doBatchUndo()">{labels_dict["undo"]}</button>
    <button class="desel-btn" onclick="doDeselAll()">{labels_dict["deselect_all"]}</button>
  </div>
</div>

<div id="zoom-tooltip">
  <img id="tooltip-img" alt="Zoomed product" referrerpolicy="no-referrer">
  <button class="tooltip-close" onclick="closeZoom()" title="Close">×</button>
</div>

<div id="prefetch-status"></div>

<script>
(function pinIframe() {{
  try {{
    var par = window.parent;
    var STYLE_ID = '__cuf_iframe_pin__';
    if (!par.document.getElementById(STYLE_ID)) {{
      var s = par.document.createElement('style');
      s.id = STYLE_ID;
      s.textContent = [
        'iframe[title="st.iframe"], iframe[title="streamlit.components.v1.html"] {{',
        '  visibility: visible !important;',
        '  opacity: 1 !important;',
        '  transition: opacity 0.2s ease-in-out;',
        '}}'
      ].join('\\n');
      par.document.head.appendChild(s);
    }}
    var OBS_KEY = '__cuf_obs__';
    if (!par.window[OBS_KEY]) {{
      var obs = new par.MutationObserver(function(mutations) {{
        mutations.forEach(function(m) {{
          if (m.type !== 'attributes' || m.attributeName !== 'style') return;
          var el = m.target;
          if (el.tagName !== 'IFRAME') return;
          if (el.style.visibility === 'hidden') {{
            el.style.setProperty('visibility', 'visible', 'important');
            el.style.setProperty('opacity', '1', 'important');
          }}
        }});
      }});
      obs.observe(par.document.body, {{
        subtree: true, attributes: true, attributeFilter: ['style']
      }});
      par.window[OBS_KEY] = obs;
    }}
  }} catch(e) {{ }}
}})();

try {{
  var par = window.parent.document;
  if (!par.window.__stModalLocked) {{
    par.window.__stModalLocked = true;
    function blockOutsideClicks(e) {{
      var dialog = par.querySelector('[data-testid="stDialog"]');
      if (dialog && !dialog.contains(e.target)) {{
        e.stopPropagation();
        e.preventDefault();
      }}
    }}
    par.addEventListener('mousedown', blockOutsideClicks, true);
    par.addEventListener('mouseup', blockOutsideClicks, true);
    par.addEventListener('click', blockOutsideClicks, true);
  }}
}} catch(e) {{ console.error("Could not lock dialog", e); }}

function escapeHtml(u){{return(u||"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");}}
var CARDS = {cards_json};
var POOR_IMG_SIDS = new Set({poor_img_sids_json});
var PREFETCH_URLS = {prefetch_json};
var PLACEHOLDER = "{_PLACEHOLDER_SVG}";
var LABELS = {labels_json};
var ALL_MATCHING_SIDS = {all_matching_sids_json};

var initialSelections = {initial_selections_json};
var selected = window._gridSelected || initialSelections.selected || {{}};
var staged = window._stagedRejections || initialSelections.staged || {{}};
var COMMITTED = window._committedRejections || initialSelections.committed || {committed_json};

window._imageIssues = window._imageIssues || {{}};
CARDS.forEach(c => {{
  if (c.warnings && c.warnings.length) {{
    if (!window._imageIssues[c.sid]) window._imageIssues[c.sid] = [];
    c.warnings.forEach(w => {{ if (!window._imageIssues[c.sid].includes(w)) window._imageIssues[c.sid].push(w); }});
  }}
}});
window._currentSort = window._currentSort || '{initial_sort}';

window._pendingUndos = window._pendingUndos || {{}};
window._undoTimer = null;

function showGhostOverlay(msgText) {{
  var ghost = document.createElement('div');
  ghost.id = '__grid_ghost__';
  ghost.style.cssText = 'position:fixed;z-index:99999;inset:0;background:rgba(255,255,255,0.85);display:flex;align-items:center;justify-content:center;font-family:sans-serif;color:#FF8800;transition:opacity 0.4s ease;';
  ghost.innerHTML = '<div style="font-size:22px;font-weight:bold;">' + msgText + '</div>';
  var existing = document.getElementById('__grid_ghost__');
  if (existing) existing.remove();
  document.body.appendChild(ghost);
  setTimeout(function() {{
    var g = document.getElementById('__grid_ghost__');
    if (g) {{ g.style.opacity = '0'; setTimeout(function() {{ if(g && g.parentNode) g.remove(); }}, 400); }}
  }}, 4000);
}}

function sendMsg(type, payload, retryCount = 0) {{
  try {{
    var par = window.parent;
    var inputs = par.document.querySelectorAll('input[type="text"]');
    var bridge = null;
    for (var i = 0; i < inputs.length; i++) {{
      if (inputs[i].getAttribute('aria-label') === 'jtbridge' || inputs[i].placeholder === 'JTBRIDGE_UNIQUE_DO_NOT_USE') {{
        bridge = inputs[i]; break;
      }}
    }}
    if (!bridge) {{
      if (retryCount < 3) {{
        setTimeout(function() {{ sendMsg(type, payload, retryCount + 1); }}, 100 * (retryCount + 1));
        return;
      }} else {{
        console.error('jtbridge: Failed to send message after 3 retries', type);
        return;
      }}
    }}
    
    var sx = par.window.scrollX || 0;
    var sy = par.window.scrollY || 0;
    
    var msg = JSON.stringify({{action: type, payload: payload}});
    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(par.HTMLInputElement.prototype, 'value').set;
    nativeInputValueSetter.call(bridge, msg);
    
    bridge.dispatchEvent(new par.Event('input', {{bubbles: true}}));
    bridge.dispatchEvent(new par.KeyboardEvent('keydown', {{bubbles:true,cancelable:true,key:'Enter',keyCode:13}}));
    bridge.dispatchEvent(new par.KeyboardEvent('keyup',   {{bubbles:true,cancelable:true,key:'Enter',keyCode:13}}));
    
    par.window.scrollTo(sx, sy);
    
    if (typeof window._stateChanges === 'undefined') window._stateChanges = [];
    window._stateChanges.push({{action: type, payload: payload, timestamp: Date.now()}});
  }} catch(ex) {{ console.error('jtbridge error:', ex); }}
}}

function scrollToTop() {{
  window.scrollTo({{top: 0, behavior: 'smooth'}});
}}

function updateParentPagination() {{
  var pending = Object.keys(selected).length + Object.keys(staged).length;
  try {{
    var par = window.parent.document;
    var buttons = par.querySelectorAll('button');
    buttons.forEach(b => {{
      var txt = b.innerText || "";
      if (txt.includes('Close') && !b.dataset.fastCloseBound) {{
        b.dataset.fastCloseBound = "true";
        b.addEventListener('click', function() {{
          var modalContainer = par.querySelector('div[data-testid="stModal"]');
          if (modalContainer) {{
            modalContainer.style.transition = 'opacity 0.15s ease-out';
            modalContainer.style.opacity = '0';
            setTimeout(() => modalContainer.style.display = 'none', 150);
          }}
        }});
      }}
      if (txt.includes('Prev Page') || txt.includes('Next Page')) {{
        if (pending > 0) {{
          b.disabled = true;
          b.style.opacity = '0.5';
          b.title = "Confirm or clear your selections before navigating.";
        }} else {{
          b.disabled = false;
          b.style.opacity = '1';
          b.title = "";
        }}
      }} else if (txt.includes('Close')) {{
        b.disabled = false;
      }}
    }});
    var inputs = par.querySelectorAll('input[type="number"]');
    inputs.forEach(inp => {{
      var wrapper = inp.closest('div[data-testid="stNumberInput"]');
      if (wrapper && wrapper.innerText.includes('Jump to Page')) {{
        if (pending > 0) {{
          wrapper.style.pointerEvents = 'none';
          wrapper.style.opacity = '0.3';
          wrapper.title = "Confirm or clear your selections before navigating.";
        }} else {{
          wrapper.style.pointerEvents = 'auto';
          wrapper.style.opacity = '1';
          wrapper.title = "";
        }}
      }}
    }});
  }} catch(e) {{}}
}}

function onImgLoad(img, sid) {{
  img.classList.remove('skeleton');
  img.classList.add('img-loaded');
  var wrap = img.closest('.card-img-wrap');
  if (wrap) wrap.classList.add('img-loaded');
  var w = img.naturalWidth, h = img.naturalHeight;
  var warns = [];
  if (w > 0 && h > 0) {{
    if (w < 200 || h < 200) warns.push('Low Resolution');
    var ratio = h / w;
    if (ratio > 1.5) warns.push('Tall (Screenshot?)');
    else if (ratio < 0.6) warns.push('Wide Aspect');
  }}
  if (warns.length) addWarnings(sid, warns);
}}

var _lazyObserver = null;
function getLazyObserver() {{
  if (_lazyObserver) return _lazyObserver;
  if (!('IntersectionObserver' in window)) return null;
  _lazyObserver = new IntersectionObserver(function(entries) {{
    entries.forEach(function(entry) {{
      if (!entry.isIntersecting) return;
      var img = entry.target;
      if (img.dataset.lazySrc) {{
        img.src = img.dataset.lazySrc;
        delete img.dataset.lazySrc;
        _lazyObserver.unobserve(img);
      }}
    }});
  }}, {{rootMargin: '200px 0px', threshold: 0.01}});
  return _lazyObserver;
}}

function activateLazyImages() {{
  var observer = getLazyObserver();
  if (!observer) return;
  document.querySelectorAll('img.card-img[data-lazy-src]').forEach(function(img) {{
    observer.observe(img);
  }});
}}

function onImgError(img, sid) {{
  var card = CARDS.find(c => c.sid === sid);
  var realSrc = img.dataset.lazySrc || (card ? card.img : '');
  if (!img.dataset.triedProxy && realSrc && realSrc.startsWith('http')) {{
    img.dataset.triedProxy = 'true';
    delete img.dataset.lazySrc;
    img.src = "https://wsrv.nl/?url=" + encodeURIComponent(realSrc);
    return;
  }}
  img.onerror = null;
  delete img.dataset.lazySrc;
  img.src = PLACEHOLDER;
  img.classList.add('img-loaded');
  if (!window._imageIssues[sid]) window._imageIssues[sid] = [];
  if (!window._imageIssues[sid].includes('Broken Image')) window._imageIssues[sid].push('Broken Image');
  addWarnings(sid, ['Broken Image']);
  var debugDiv = document.getElementById('debug-' + escapeHtml(sid));
  if (debugDiv) {{
    debugDiv.style.display = 'block';
    debugDiv.innerHTML = "<b>FAILED URL:</b><br>" + escapeHtml(realSrc);
  }}
}}

function addWarnings(sid, warns) {{
  var wrap = document.querySelector('#card-' + escapeHtml(sid) + ' .warn-wrap');
  if (!wrap) return;
  warns.forEach(w => {{
    var badge = document.createElement('span');
    badge.className = 'warn-badge';
    badge.textContent = w;
    wrap.appendChild(badge);
  }});
  if (!window._imageIssues[sid]) window._imageIssues[sid] = [];
  warns.forEach(w => {{ if (!window._imageIssues[sid].includes(w)) window._imageIssues[sid].push(w); }});
}}

function buildCardActionsHtml(safeSid, warnings, cardData) {{
  var card = cardData || {{}};
  var FLAG_MAP = {{
    'Wrong Category':         ['REJECT_WRONG_CAT',     LABELS.wrong_cat],
    'Category Check':         ['REJECT_WRONG_CAT',     LABELS.wrong_cat],
    'Missing COLOR':          ['REJECT_COLOR',          LABELS.missing_color],
    'Color Check':            ['REJECT_COLOR',          LABELS.missing_color],
    'Restricted Brand':       ['REJECT_BRAND',          LABELS.restr_brand],
    'Restricted brands':      ['REJECT_BRAND',          LABELS.restr_brand],
    'Prohibited':             ['REJECT_PROHIBITED',     LABELS.prohibited],
    'Prohibited products':    ['REJECT_PROHIBITED',     LABELS.prohibited],
    'Wrong Brand':            ['REJECT_WRONG_BRAND',    LABELS.wrong_brand],
    'Suspected Fake product': ['REJECT_FAKE',           LABELS.fake_prod],
    'Poor images':            ['REJECT_POOR_IMAGE',     LABELS.poor_img],
    'Image Quality Check':    ['REJECT_POOR_IMAGE',     LABELS.poor_img],
    'Brand Image Check':      ['REJECT_POOR_IMAGE',     LABELS.poor_img],
    'Product Warranty':       ['REJECT_WARRANTY',       'Product Warranty'],
    'Warranty Check':         ['REJECT_WARRANTY',       'Product Warranty'],
    'FDA':                    ['REJECT_FDA',            'FDA'],
    'Wrong Variation':        ['REJECT_VARIATION',      'Wrong Variation'],
    'Variation Check':        ['REJECT_VARIATION',      'Wrong Variation'],
    'BRAND name repeated in NAME': ['REJECT_BRAND_IN_NAME', 'Brand in Name'],
    'Product Name Brand Name':     ['REJECT_BRAND_IN_NAME', 'Brand in Name'],
    'Title Language Check':   ['REJECT_TITLE_LANG',    'Title Language'],
  }};
  var defaultCode  = 'REJECT_POOR_IMAGE';
  var defaultLabel = LABELS.poor_img;
  for (var i = 0; i < (warnings||[]).length; i++) {{
    var match = FLAG_MAP[warnings[i]];
    if (match) {{ defaultCode = match[0]; defaultLabel = match[1]; break; }}
  }}
  var opts = [
    ['REJECT_POOR_IMAGE',    LABELS.poor_img],
    ['REJECT_IMG_STRETCHED', 'Image Stretched'],
    ['REJECT_IMG_BLURRY',    'Image Blurry'],
    ['REJECT_IMG_MISMATCH',  'Image Mismatch'],
    ['REJECT_IMG_INFRINGING','Image Infringing'],
    ['REJECT_IMG_TOO_MANY',  'Image Too Many Things'],
    ['REJECT_WRONG_CAT',     escapeHtml(LABELS.wrong_cat)],
    ['REJECT_FAKE',          escapeHtml(LABELS.fake_prod)],
    ['REJECT_BRAND',         escapeHtml(LABELS.restr_brand)],
    ['REJECT_PROHIBITED',    escapeHtml(LABELS.prohibited)],
    ['REJECT_COLOR',         escapeHtml(LABELS.missing_color)],
    ['REJECT_WRONG_BRAND',   escapeHtml(LABELS.wrong_brand)],
    ['REJECT_DUPLICATE',     'Duplicate product'],
    ['REJECT_WEIGHT_VOL',    'Missing Weight/Volume'],
    ['REJECT_BRAND_REPEAT',  'BRAND name repeated in NAME'],
    ['REJECT_FAKE_PERFUME',  'Suspected Fake Perfume'],
    ['OTHER_CUSTOM',         'Other Reason (Custom)'],
  ];
  var optionsHtml = opts.map(function(o) {{
    return `<option value="${{o[0]}}">${{o[1]}}</option>`;
  }}).join('');
  var autoCommentHtml = '';
  if (defaultCode === 'REJECT_WRONG_CAT' && (card.ai_caption || card.suggested_cat || card.cat_reason)) {{
    var parts = [];
    if (card.cat_reason) parts.push(card.cat_reason);
    else if (card.ai_caption) parts.push(card.ai_caption);
    if (card.suggested_cat) parts.push('Suggested: ' + card.suggested_cat);
    var autoTxt = parts.join(' | ').slice(0, 250);
    autoCommentHtml = `<textarea class="auto-comment" id="ac-${{safeSid}}" onclick="event.stopPropagation()" rows="2" style="width:100%;font-size:10px;margin-top:4px;padding:4px 6px;border-radius:6px;border:1px solid #e5e7eb;resize:vertical;background:#fffbf5;color:#333;">${{escapeHtml(autoTxt)}}</textarea>`;
  }}

  return (
    `<div class="acts">` +
      `<button class="act-btn" onclick="event.stopPropagation();window.stageRejectWithComment('${{safeSid}}','${{defaultCode}}')">` +
        escapeHtml(defaultLabel) +
      `</button>` +
      `<select class="act-more" onchange="if(this.value){{event.stopPropagation();window.stageRejectWithComment('${{safeSid}}',this.value);this.value=''}}">` +
        `<option value="">${{escapeHtml(LABELS.more_options)}}</option>` +
        optionsHtml +
      `</select>` +
      autoCommentHtml +
    `</div>`
  );
}}

var UNNECESSARY_WORDS = {_js_json(support_files.get("unnecessary_words", []))};
var PROHIBITED_WORDS = {_js_json(support_files.get("prohibited_words", []))};
var SELLER_TRUST = {_js_json(seller_trust)};

function getHighlightedName(card) {{
  var name = card.name;
  var warns = card.warnings || [];
  var words = [];
  if (warns.includes("Unnecessary words")) words = words.concat(UNNECESSARY_WORDS);
  if (warns.includes("Prohibited Words")) words = words.concat(PROHIBITED_WORDS);
  if (warns.includes("BRAND name repeated in NAME")) words.push(card.brand);
  
  if (words.length === 0) return card.name.length > 38 ? escapeHtml(card.name.slice(0,38)) + '\u2026' : escapeHtml(card.name);
  
  words.sort((a,b) => b.length - a.length);
  var regex = new RegExp('(' + words.map(w => w.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&')).join('|') + ')', 'gi');
  var hName = name.replace(regex, '<span class="hlt">$1</span>');
  return hName;
}}

window.rejectAllFromSeller = function(seller) {{
  var sids = CARDS.filter(c => c.seller === seller).map(c => c.sid);
  sids.forEach(sid => {{
    if (!(sid in staged)) {{
      if (sid in selected) delete selected[sid];
      staged[sid] = "Bulk Seller Reject (High Risk)";
      replaceCard(sid);
    }}
  }});
  updateSelCount();
}};

function renderCard(card) {{
  var sid = card.sid;
  var safeSid = sid.replace(/'/g, "\\\\'");
  var isCommitted = sid in COMMITTED;
  var isStaged = sid in staged;
  var isSelected = sid in selected;
  var isPoorImgRej = isCommitted && POOR_IMG_SIDS.has(sid);
  var isBrandImgRej = isCommitted && (String(COMMITTED[sid]).includes('Brand Image Check'));
  var cls = 'card' + (isCommitted ? ' committed-rej' + (isPoorImgRej ? ' poor-img-rej' : '') + (isBrandImgRej ? ' brand-image-rej' : '') : isStaged ? ' staged-rej' : '') + (isSelected ? ' selected' : '') + (card.is_in_zip ? ' zip-product' : '');

  var safeImgSrcForHtml = card.img ? card.img.replace(/'/g, "%27").replace(/"/g, "%22") : PLACEHOLDER;
  var shortName = card.name.length > 38 ? escapeHtml(card.name.slice(0,38)) + '\u2026' : escapeHtml(card.name);
  var warnHtml = (card.warnings || []).map(w => {{
    var isCrit = w.toLowerCase().includes('fake') || w.toLowerCase().includes('restricted') || w.toLowerCase().includes('prohibited') || w.toLowerCase().includes('counterfeit') || w.toLowerCase().includes('infringing');
    var cls = 'warn-badge' + (isCrit ? ' critical' : '');
    return `<span class="${{cls}}">${{escapeHtml(w)}}</span>`;
  }}).join('');
  if (card.is_duplicate) warnHtml += `<span class="warn-badge duplicate">DUPLICATE</span>`;
  if (card.is_manual_review) warnHtml += `<span class="warn-badge manual">MANUAL REVIEW</span>`;
  if (card.color_mismatch) warnHtml += `<span class="warn-badge mismatch" title="${{escapeHtml(card.color_mismatch)}}">Color Mismatch</span>`;
  var priceHtml = card.price ? `<div class="price-badge">${{escapeHtml(card.price)}}</div>` : '';
  var colorHtml = card.color ? `<div class="co" title="Color: ${{escapeHtml(card.color)}}">Color: ${{escapeHtml(card.color)}}</div>` : '';
  var colorMismatchHtml = card.color_mismatch ? `<div class="co" style="color:#b45309;border-color:#fde68a;" title="${{escapeHtml(card.color_mismatch)}}">${{escapeHtml(card.color_mismatch)}}</div>` : '';
  var catReasonHtml = (card.cat_reason && (card.warnings||[]).some(w => w.includes('Category'))) ?
    `<div class="co" style="color:#9333ea;font-size:10px;white-space:normal;line-height:1.3;" title="${{escapeHtml(card.cat_reason)}}">${{escapeHtml(card.cat_reason.length > 80 ? card.cat_reason.slice(0,80)+'…' : card.cat_reason)}}</div>` : '';
  var suggestedCatHtml = card.suggested_cat ? `<div class="co" style="color:#0369a1;" title="AI suggests: ${{escapeHtml(card.suggested_cat)}}">→ ${{escapeHtml(card.suggested_cat.length > 50 ? card.suggested_cat.slice(0,50)+'…' : card.suggested_cat)}}</div>` : '';
  var brandDetectedHtml = (isBrandImgRej && card.brand_detected) ? `<div class="co" style="background:#E8F5E9;color:#2E7D32;border:1px solid #C8E6C9;" title="Brand Detected: ${{escapeHtml(card.brand_detected)}}">Detected Brand: ${{escapeHtml(card.brand_detected)}}</div>` : '';

  var zoomHtml = `<button class="zoom-btn" onclick="event.stopPropagation();showZoom('${{safeSid}}', event)" title="Preview">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      <line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>
    </svg></button>`;

  var imgIdx = CARDS.indexOf(card);
  var isEager = imgIdx < {cols_per_row * 4};
  var loadingAttr = isEager ? 'eager' : 'lazy';
  var priorityAttr = isEager ? 'fetchpriority="high"' : 'fetchpriority="low"';
  var imgSrcAttr = isEager
    ? `src="${{safeImgSrcForHtml}}"`
    : `src="${{PLACEHOLDER}}" data-lazy-src="${{safeImgSrcForHtml}}"`;

  var overlayHtml = '', actHtml = '';
    if (isCommitted) {{
      var rejReason = (COMMITTED[sid]||'').replace(/_/g,' ');
      var actionLabel = isBrandImgRej ? LABELS.approve : LABELS.undo;
      var extraInfo = '';
      if (isBrandImgRej && card.brand_detected) {{
        extraInfo = `<div style="margin-top:auto; padding:6px 8px; background:rgba(211,47,47,0.75); border-radius:0 0 8px 8px; color:#fff; font-weight:800; font-size:12px; width:100%; text-align:center; position:absolute; bottom:0; left:0;">Detected Brand: ${{escapeHtml(card.brand_detected)}}</div>`;
      }}

      if (isBrandImgRej) {{
        overlayHtml = `<div class="rej-overlay">
          <div class="rej-badge">${{escapeHtml(LABELS.rejected)}}</div>
          <div class="rej-label">${{escapeHtml(rejReason)}}</div>
          ${{extraInfo}}
        </div>`;
        actHtml = `<div class="acts">
          <button class="act-btn" style="background:#4CAF50; color:#fff; flex:1;" onclick="event.stopPropagation();window.undoReject('${{safeSid}}')">${{escapeHtml(actionLabel)}}</button>
        </div>`;
      }} else {{
        overlayHtml = `<div class="rej-overlay">
          <div class="rej-badge">${{escapeHtml(LABELS.rejected)}}</div>
          <div class="rej-label">${{escapeHtml(rejReason)}}</div>
          <button class="undo-btn" onclick="event.stopPropagation();window.undoReject('${{safeSid}}')">${{escapeHtml(actionLabel)}}</button>
          ${{extraInfo}}
        </div>`;
      }}
  }} else if (isStaged) {{
    overlayHtml = `<div class="rej-overlay staged">
      <div class="rej-badge pending">${{escapeHtml(LABELS.rejected)}}</div>
      <div class="rej-label">Pending reason:<br>${{escapeHtml((staged[sid]||'').replace(/_/g,' '))}}</div>
      <button class="undo-btn" onclick="event.stopPropagation();window.clearStaged('${{safeSid}}')">${{escapeHtml(LABELS.clear_sel)}}</button>
    </div>`;
  }} else {{
    actHtml = buildCardActionsHtml(safeSid, card.warnings, card);
  }}

    var trustBadge = '';
    var score = SELLER_TRUST[card.seller] || 0;
    if (score > 80) {{
      trustBadge = `<div class="trust-badge" onclick="event.stopPropagation();window.rejectAllFromSeller('${{card.seller.replace(/'/g,"\\\\'")}}')" title="Seller has ${{score}}% rejection rate. Click to reject all from this seller.">High Risk Seller</div>`;
    }}

  var dataAttrs = 'data-sid="' + escapeHtml(String(card.data_sid||'')) + '" data-name="' + escapeHtml(String(card.data_name||'')) + '" data-brand="' + escapeHtml(String(card.data_brand||'')) + '" data-cat="' + escapeHtml(String(card.data_cat||'')) + '"';
  return `<div class="${{cls}}" id="card-${{escapeHtml(sid)}}" ${{dataAttrs}} tabindex="0" onclick="window.toggleSelect('${{safeSid}}',event)">
    <div class="card-img-wrap">
      ${{trustBadge}}
      ${{priceHtml}}
      <div class="warn-wrap">${{warnHtml}}</div>
      <div id="debug-${{escapeHtml(sid)}}" class="debug-hud"></div>
      <img class="card-img-placeholder" src="${{PLACEHOLDER}}" alt="">
      <img class="card-img" ${{imgSrcAttr}} decoding="async" loading="${{loadingAttr}}" ${{priorityAttr}} referrerpolicy="no-referrer"
            onload="onImgLoad(this,'${{safeSid}}')" onerror="onImgError(this,'${{safeSid}}')">
      ${{zoomHtml}}
      ${{overlayHtml}}
      <div class="tick">\u2714</div>
    </div>
    <div class="meta">
      <div class="nm" title="${{escapeHtml(card.name)}}">${{getHighlightedName(card)}}</div>
      <div class="br" title="${{escapeHtml(card.brand)}}">Brand: ${{escapeHtml(card.brand)}}</div>
      <div class="ct" title="${{escapeHtml(card.cat)}}">Category: ${{escapeHtml(card.cat)}}</div>
      <div class="sl" title="${{escapeHtml(card.seller)}}">
        <span>Seller: ${{escapeHtml(card.seller)}}</span>
        ${{card.is_in_zip ? `<span style="background: #3b82f6; color: #fff; font-size: 9px; font-weight: 900; padding: 2px 7px; border-radius: 4px; letter-spacing: 0.5px; box-shadow: 0 1px 4px rgba(59,130,246,0.4);">ZIP</span>` : ''}}
      </div>
      ${{colorHtml}}
      ${{colorMismatchHtml}}
      ${{catReasonHtml}}
      ${{suggestedCatHtml}}
      ${{brandDetectedHtml}}
    </div>
    ${{actHtml}}
  </div>`;
}}

window.showZoom = function(sid, event) {{
  var tooltip = document.getElementById('zoom-tooltip');
  if (tooltip.style.display === 'block' && window.currentZoomSid === sid) {{
    closeZoom();
    return;
  }}
  var card = CARDS.find(c => c.sid === sid);
  if (!card) return;
  var img = document.getElementById('tooltip-img');
  img.src = card.img || PLACEHOLDER;
  img.onerror = function() {{ img.src = PLACEHOLDER; img.onerror = null; }};
  tooltip.style.display = 'block';
  window.currentZoomSid = sid;
}};

window.closeZoom = function() {{
  document.getElementById('zoom-tooltip').style.display = 'none';
  window.currentZoomSid = null;
}};

document.addEventListener('click', function(e) {{
  var tooltip = document.getElementById('zoom-tooltip');
  if (tooltip.style.display === 'block' && !tooltip.contains(e.target) && !e.target.closest('.zoom-btn')) {{
    closeZoom();
  }}
}});

function updateSelCount() {{
  var pendingCount = (Object.keys(selected).length + Object.keys(staged).length);
  var pendingText = pendingCount + ' ' + LABELS.items_pending;
  document.querySelectorAll('.sel-count-text').forEach(el => el.textContent = pendingText);

  var drawer = document.getElementById('action-drawer');
  if (drawer) {{
    if (pendingCount > 0) drawer.classList.add('expanded');
    else drawer.classList.remove('expanded');
  }}

  var btmDrawer = document.getElementById('bottom-action-drawer');
  if (btmDrawer) {{
    if (pendingCount > 0) btmDrawer.classList.add('expanded');
    else btmDrawer.classList.remove('expanded');
  }}

  updateParentPagination();
  try {{
    var stateSnapshot = {{
      selected: Object.assign({{}}, selected),
      staged: Object.assign({{}}, staged),
      committed: Object.assign({{}}, COMMITTED)
    }};
    var par = window.parent;
    if (par && par.sessionStorage) {{
      try {{ par.sessionStorage.setItem('_iframe_page_state_' + (window._pageNumber || 0), JSON.stringify(stateSnapshot)); }} catch(e) {{}}
    }}
    sendMsg('update_selections', stateSnapshot);
  }} catch(e) {{}}
}}

window._currentFilter = window._currentFilter || '{initial_filter}';

function getSortedCards() {{
  var sort = window._currentSort;
  if (!sort) return CARDS;
  var CLIENT_IMAGE_SORTS = ['low_res', 'tall', 'wide', 'broken', 'no_issue', 'most_flagged'];
  if (CLIENT_IMAGE_SORTS.includes(sort)) {{
    var ISSUE_MAP = {{ 'low_res':'Low Resolution','tall':'Tall (Screenshot?)','wide':'Wide Aspect','broken':'Broken Image' }};
    var sorted = CARDS.slice();
    if (sort === 'no_issue') {{
      sorted.sort(function(a,b) {{ return ((window._imageIssues[a.sid]||[]).length>0?1:0) - ((window._imageIssues[b.sid]||[]).length>0?1:0); }});
    }} else if (sort === 'most_flagged') {{
      sorted.sort(function(a,b) {{ return (b.warnings||[]).length - (a.warnings||[]).length; }});
    }} else {{
      var target = ISSUE_MAP[sort] || sort;
      sorted.sort(function(a,b) {{ return ((window._imageIssues[a.sid]||[]).includes(target)?0:1) - ((window._imageIssues[b.sid]||[]).includes(target)?0:1); }});
    }}
    return sorted;
  }}
  return CARDS;
}}

function getDisplayCards() {{
  var cards = getSortedCards();
  var gs = document.getElementById('grid-search');
  var q = gs ? gs.value.toLowerCase().trim() : '';
  if (q) {{
    var terms = q.split(',').map(function(t) {{ return t.trim(); }}).filter(Boolean);
    if (terms.length > 0) {{
      cards = cards.filter(function(c) {{
        var text = ((c.name || '') + ' ' + (c.brand || '') + ' ' + (c.sid || '') + ' ' + (c.cat || '') + ' ' + (c.seller || '')).toLowerCase();
        return terms.some(function(t) {{ return text.includes(t); }});
      }});
    }}
  }}
  var f = window._currentFilter;
  if (!f) return cards;
  var CLIENT_IMAGE_FILTERS = ['Poor images', 'Low Resolution', 'Tall (Screenshot?)', 'Wide Aspect', 'Broken Image'];
  if (CLIENT_IMAGE_FILTERS.includes(f)) {{
    return cards.filter(function(c) {{
      return (c.warnings||[]).includes(f) || (window._imageIssues[c.sid]||[]).includes(f);
    }});
  }}
  return cards;
}}

window.applySort = function(val) {{
  window._currentSort = val;
  ['sort-sel-top','sort-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value=val; }});
  renderAll();
  sendMsg('set_sort', val);
}};

window.applyFilter = function(val) {{
  window._currentFilter = val;
  ['filter-sel-top','filter-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value=val; }});
  renderAll();
  sendMsg('set_filter', val);
}};

function renderAll() {{
  var cards = getDisplayCards();
  document.getElementById('card-grid').innerHTML = cards.map(renderCard).join('');
  var gs = document.getElementById('grid-search');
  var q = gs ? gs.value.toLowerCase().trim() : '';
  var countEl = document.getElementById('grid-count');
  if (countEl) {{
    countEl.textContent = (q || window._currentFilter)
      ? cards.length + ' matching'
      : cards.length + ' products';
  }}
  updateSelCount(); activateLazyImages();
}}

function replaceCard(sid) {{
  var cards = getDisplayCards();
  var card = cards.find(c => c.sid === sid);
  if (!card) {{
    card = CARDS.find(c => c.sid === sid);
  }}
  if (!card) return;
  var el = document.getElementById('card-' + escapeHtml(sid));
  if (!el) return;
  var t = document.createElement('div');
  t.innerHTML = renderCard(card);
  el.replaceWith(t.firstElementChild);
  activateLazyImages();
}}

window.doSelectAll = function() {{
  ALL_MATCHING_SIDS.forEach(sid => {{ if (!(sid in staged)) selected[sid] = true; }});
  renderAll();
  updateSelCount();
}};

window.toggleSelect = function(sid, e) {{
  var t = e && e.target;
  if (t && (t.tagName === 'SELECT' || t.tagName === 'OPTION' || t.tagName === 'BUTTON' || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.closest('select,button,input,textarea,a'))) return;
  if (sid in staged) delete staged[sid];
  else if (sid in selected) delete selected[sid];
  else selected[sid] = true;
  _focusedSid = sid;
  document.querySelectorAll('.card').forEach(function(c) {{ c.classList.remove('keyboard-focused'); }});
  replaceCard(sid); updateSelCount();
  var el = document.getElementById('card-' + escapeHtml(sid));
  if (el) el.classList.add('keyboard-focused');
}};

window.stageRejectWithComment = function(sid, r) {{
  var safeSid = sid.replace(/'/g, "\\\\'");
  var ta = document.getElementById('ac-' + safeSid);
  if (ta && ta.value.trim()) {{
    window._autoComments = window._autoComments || {{}};
    window._autoComments[sid] = ta.value.trim();
  }}
  window.stageReject(sid, r);
}};

window.stageReject = function(sid, r) {{
  var currentCard = CARDS.find(c => c.sid === sid);
  var toStage = [sid];

  if (sid in selected) {{
      Object.keys(selected).forEach(s => {{
          if (!toStage.includes(s)) toStage.push(s);
      }});
  }}

  if (currentCard && (r === 'REJECT_POOR_IMAGE' || r.startsWith('REJECT_IMG_'))) {{
      CARDS.forEach(c => {{
          if (c.sid !== sid && (c.img === currentCard.img || (c.hash && c.hash === currentCard.hash))) {{
              toStage.push(c.sid);
          }}
      }});
  }}

  if (r === 'REJECT_WRONG_CAT' && currentCard) {{
      var nameTokens = currentCard.name.toLowerCase().split(/[^\\w]+/).filter(w => w.length > 4);
      if (nameTokens.length > 0) {{
          CARDS.forEach(c => {{
              if (c.sid !== sid && !(c.sid in staged)) {{
                  var cName = c.name.toLowerCase();
                  var matchCount = nameTokens.filter(t => cName.includes(t)).length;
                  if (matchCount >= 2 || (nameTokens.length === 1 && cName.includes(nameTokens[0]))) {{
                      addWarnings(c.sid, ["Potential Category Issue"]);
                      var el = document.getElementById('card-' + escapeHtml(c.sid));
                      if (el) {{
                          el.style.border = '2px solid #d97706';
                          setTimeout(() => {{ if (el) el.style.border = ''; }}, 4000);
                      }}
                  }}
              }}
          }});
      }}
  }}

  if (r === 'OTHER_CUSTOM') {{
    showCustomReasonPanel(function(cmt) {{
      if (!cmt) return;
      var reason = "Other Reason (Custom): " + cmt;
      toStage.forEach(s => {{
          if (s in selected) delete selected[s];
          staged[s] = reason;
          replaceCard(s);
      }});
      updateSelCount();
    }});
    return;
  }}

  toStage.forEach(s => {{
      if (s in selected) delete selected[s];
      staged[s] = r;
      replaceCard(s);
  }});
  updateSelCount();
}};

window.clearStaged = function(sid) {{
    delete staged[sid];
    var cardEl = document.getElementById('card-' + escapeHtml(sid));
    if (cardEl) {{
        cardEl.classList.remove('staged-rej');
        var overlay = cardEl.querySelector('.rej-overlay.staged');
        if (overlay) overlay.remove();
    }}
    updateSelCount();
}};

window.undoReject = function(sid) {{
  delete COMMITTED[sid];
  window._pendingUndos[sid] = true;
  if (sid in selected) delete selected[sid];

  var safeSid = sid.replace(/'/g, "\\\\'");
  var cardEl = document.getElementById('card-' + escapeHtml(sid));

  if (cardEl) {{
      cardEl.classList.remove('committed-rej', 'poor-img-rej');

      var overlay = cardEl.querySelector('.rej-overlay');
      if (overlay) overlay.remove();

      var acts = cardEl.querySelector('.acts');
      if (acts) acts.remove();
      var _c = CARDS.find(c=>c.sid===safeSid)||{{}};
      cardEl.insertAdjacentHTML('beforeend', buildCardActionsHtml(safeSid, _c.warnings, _c));

      cardEl.classList.add('undo-processing');
  }}

  updateSelCount();

  try {{
    var fe = window.frameElement;
    if (fe) {{
      fe.dataset.pinnedHeight = fe.offsetHeight;
      fe.style.setProperty('min-height', fe.offsetHeight + 'px', 'important');
      if (fe.parentElement) {{
          fe.parentElement.style.setProperty('min-height', fe.offsetHeight + 'px', 'important');
      }}
    }}
  }} catch(e) {{}}

  if (window._undoTimer) clearTimeout(window._undoTimer);
  window._undoTimer = setTimeout(function() {{
    var payload = Object.assign({{}}, window._pendingUndos);
    window._pendingUndos = {{}};
    if (!Object.keys(payload).length) return;

    requestAnimationFrame(function() {{
      requestAnimationFrame(function() {{
        sendMsg('undo', payload);

        setTimeout(function() {{
          try {{
            var fe = window.frameElement;
            if (fe) {{
              fe.style.removeProperty('min-height');
              delete fe.dataset.pinnedHeight;
              if (fe.parentElement) fe.parentElement.style.removeProperty('min-height');
            }}
          }} catch(e) {{}}
          document.querySelectorAll('.card.undo-processing').forEach(function(c) {{
            c.classList.remove('undo-processing');
          }});
        }}, 1000); 
      }});
    }});
  }}, 400);
}};

window.doBatchReject = function(pos) {{
  var selectId = pos === 'top' ? 'batch-reason-top' : (pos === 'drawer' ? 'batch-reason-drawer' : 'batch-reason-bottom');
  var sel = document.getElementById(selectId);
  var br = sel.value;
  if (br === 'OTHER_CUSTOM') {{
    showCustomReasonPanel(function(cmt) {{
      if (!cmt) {{ sel.value = "REJECT_POOR_IMAGE"; return; }}
      _applyBatchReject("Other Reason (Custom): " + cmt);
      sel.value = "REJECT_POOR_IMAGE";
    }});
    return;
  }}
  _applyBatchReject(br);
}};

function _applyBatchReject(br) {{
  var payload = {{}}, count = 0;
  var autoC = window._autoComments || {{}};
  for (var s in staged) {{ payload[s] = staged[s]; count++; }}
  for (var s in selected) {{
    payload[s] = br; count++;
  }}
  var commentPayload = {{}};
  for (var s in payload) {{ if (autoC[s]) commentPayload[s] = autoC[s]; }}
  if (count === 0) {{
    for (var s in selected) delete selected[s];
    for (var s in staged) delete staged[s];
    updateSelCount();
    return;
  }}
  var allSids = Object.assign({{}}, selected, staged);
  for (var s in payload) {{ COMMITTED[s] = payload[s]; }}
  for (var s in allSids) {{ delete selected[s]; delete staged[s]; }}
  showGhostOverlay('Applying rejections...');
  renderAll();
  updateSelCount();
  if (Object.keys(commentPayload).length) {{
    sendMsg('reject_with_comments', {{reject: payload, comments: commentPayload}});
  }} else {{
    sendMsg('reject', payload);
  }}
}}

var _customReasonCallback = null;
function showCustomReasonPanel(callback) {{
  _customReasonCallback = callback;
  var panel = document.getElementById('custom-reason-panel');
  var input = document.getElementById('custom-reason-input');
  input.value = '';
  panel.style.display = 'block';
  setTimeout(function() {{ input.focus(); }}, 50);
}}
function confirmCustomReason() {{
  var input = document.getElementById('custom-reason-input');
  var val = input.value.trim();
  document.getElementById('custom-reason-panel').style.display = 'none';
  if (_customReasonCallback) {{ _customReasonCallback(val); _customReasonCallback = null; }}
}}
function cancelCustomReason() {{
  document.getElementById('custom-reason-panel').style.display = 'none';
  _customReasonCallback = null;
}}
document.getElementById('custom-reason-input').addEventListener('keydown', function(e) {{
  if (e.key === 'Enter') confirmCustomReason();
  if (e.key === 'Escape') cancelCustomReason();
}});

window.doBatchUndo = function() {{
  if (window._undoTimer) {{ clearTimeout(window._undoTimer); window._undoTimer = null; }}
  var payload = Object.assign({{}}, window._pendingUndos);
  window._pendingUndos = {{}};
  var count = 0;
  for (var s in selected) {{
    if (s in COMMITTED) {{ payload[s] = true; count++; }}
  }}
  if (Object.keys(payload).length === 0) {{
    for (var s in selected) delete selected[s];
    updateSelCount();
    return;
  }}
  for (var s in payload) {{ delete COMMITTED[s]; }}
  for (var s in selected) {{ delete selected[s]; }}

  renderAll();
  updateSelCount();
  sendMsg('undo', payload);
}};

window.doDeselAll = function() {{ for (var k in selected) delete selected[k]; for (var k in staged) delete staged[k]; renderAll(); updateSelCount(); }};

(function() {{
  if (!PREFETCH_URLS || !PREFETCH_URLS.length) return;
  var statusEl = document.getElementById('prefetch-status');
  var POOL_SIZE = 10;
  var pool = [];
  for (var p = 0; p < POOL_SIZE; p++) {{
    var pi = new Image();
    pi.referrerPolicy = "no-referrer";
    pi.style.cssText = 'width:1px;height:1px;opacity:0;position:absolute;pointer-events:none;';
    document.body.appendChild(pi);
    pool.push(pi);
  }}
  var i = 0, done = 0, total = PREFETCH_URLS.length, slot = 0;
  var runner = window.requestIdleCallback || function(fn){{setTimeout(fn,300);}};
  function prefetchBatch() {{
    var limit = POOL_SIZE, processed = 0;
    while (i < total && processed < limit) {{
      var url = PREFETCH_URLS[i++]; processed++;
      var img = pool[slot % POOL_SIZE]; slot++;
      img.onload = (function(u) {{ return function() {{
        done++;
        if (statusEl) statusEl.textContent = 'Prefetched ' + done + '/' + total;
      }}; }})(url);
      img.onerror = img.onload;
      img.src = url;
    }}
    if (i < total) runner(prefetchBatch);
  }}
  setTimeout(prefetchBatch, 800);
}})();

window.addEventListener("scroll", function() {{
  sessionStorage.setItem("__inner_iframe_scroll__", window.scrollY);
}});

{scroll_js}

var _focusedSid = null;
var _lastReason = 'REJECT_POOR_IMAGE';

function _getCardSids() {{
  return getSortedCards().map(function(c) {{ return c.sid; }});
}}

function _moveFocus(dir) {{
  var sids = _getCardSids();
  if (!sids.length) return;
  var idx = _focusedSid ? sids.indexOf(_focusedSid) : -1;
  idx = Math.max(0, Math.min(sids.length - 1, idx + dir));
  _focusedSid = sids[idx];
  document.querySelectorAll('.card').forEach(function(c) {{
    c.style.outline = '';
    c.classList.remove('keyboard-focused');
  }});
  var el = document.getElementById('card-' + escapeHtml(_focusedSid));
  if (el) {{
    el.classList.add('keyboard-focused');
    el.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
  }}
}}

document.addEventListener('keydown', function(e) {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  if (document.getElementById('custom-reason-panel').style.display === 'block') return;
  if (e.key === 'ArrowRight') {{ e.preventDefault(); _moveFocus(1); }}
  else if (e.key === 'ArrowLeft') {{ e.preventDefault(); _moveFocus(-1); }}
  else if ((e.key === 'a' || e.key === 'A') && _focusedSid) {{
    delete selected[_focusedSid]; delete staged[_focusedSid];
    replaceCard(_focusedSid); updateSelCount();
  }}
  else if ((e.key === 'r' || e.key === 'R') && _focusedSid) {{
    var sid = _focusedSid;
    if (_lastReason === 'OTHER_CUSTOM') {{
      showCustomReasonPanel(function(cmt) {{
        if (!cmt) return;
        staged[sid] = 'Other Reason (Custom): ' + cmt;
        replaceCard(sid); updateSelCount();
      }});
    }} else {{
      if (sid in selected) delete selected[sid];
      staged[sid] = _lastReason;
      replaceCard(sid); updateSelCount();
    }}
  }}
  else if (e.key === ' ' && _focusedSid) {{
    e.preventDefault();
    window.toggleSelect(_focusedSid, e);
  }}
}});

['batch-reason-top','batch-reason-bottom','batch-reason-drawer'].forEach(function(id) {{
  var el = document.getElementById(id);
  if (el) el.addEventListener('change', function() {{ _lastReason = this.value; }});
}});

window.clearSearch = function() {{
  var gs = document.getElementById('grid-search');
  if(gs) gs.value = '';
  renderAll();
  sendMsg('set_search_term', {{ term: '' }});
}};

(function() {{
  var _gs = document.getElementById('grid-search');
  if (_gs) {{
    _gs.addEventListener('input', function() {{
      renderAll();
      var searchTerm = _gs.value.toLowerCase().trim();
      sendMsg('set_search_term', {{ term: searchTerm }});
    }});
  }}
}})();

window.applyDark = function(on) {{
  document.documentElement.style.setProperty('--bg',    on ? '#18181b' : '#f9fafb');
  document.documentElement.style.setProperty('--card',  on ? '#27272a' : '#ffffff');
  document.documentElement.style.setProperty('--text',  on ? '#f4f4f5' : '#111827');
  document.documentElement.style.setProperty('--border',on ? '#3f3f46' : '#e5e7eb');
  var dtEl = document.getElementById('dark-toggle');
  if (dtEl) dtEl.textContent = on ? 'Light' : 'Dark';
  try {{ localStorage.setItem('gridDark', on ? '1' : '0'); }} catch(e) {{}}
}}
window.toggleDark = function() {{ _dark = false; applyDark(_dark); }}

window.batchApproveSingle = function(sid) {{
  window.parent.postMessage({{type:'staged_reject', sid:sid, reason:'Approved'}}, '*');
}}

window.batchApprove = function() {{
  var sids = Object.keys(selected).filter(s => !(s in COMMITTED));
  if (sids.length === 0) return;
  if (confirm('Approve ' + sids.length + ' selected items?')) {{
    var payload = {{}};
    sids.forEach(sid => {{ payload[sid] = 'Approved'; }});
    var allSids = Object.assign({{}}, selected);
    for (var s in payload) {{ COMMITTED[s] = payload[s]; }}
    for (var s in allSids) {{ delete selected[s]; }}
    showGhostOverlay('Approving...');
    renderAll();
    updateSelCount();
    sendMsg('approve', payload);
  }}
}}

try {{
  ['sort-sel-top','sort-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value = window._currentSort; }});
  ['filter-sel-top','filter-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value = window._currentFilter; }});
  renderAll();
}} catch(e) {{
  document.getElementById('card-grid').innerHTML = '<div style="color:red;padding:20px;font-size:14px;font-family:monospace;white-space:pre-wrap;background:#fff3f3;border:2px solid red;border-radius:8px;margin:20px;">&#x26A0; JS ERROR in renderAll():<br>' + String(e) + '<br><br>Stack:<br>' + (e.stack||'') + '</div>';
}}
</script>
</body>
</html>"""

@st.fragment
def _visual_grid_fragment(support_files, brand_image_check_sids, fr):
    """Fragment: owns all pagination + iframe rendering.
    Uses st.rerun(scope='fragment') so the iframe never disappears on page turns."""

    if "grid_page_selections" not in st.session_state:
        st.session_state.grid_page_selections = {}
    if "grid_search_term" not in st.session_state:
        st.session_state.grid_search_term = ""

    review_data = st.session_state.get("_vr_data")
    if review_data is None or (hasattr(review_data, "empty") and review_data.empty):
        st.info("No products to display.")
        return

    # Consume scroll-to-top flag inside the fragment so it works with fragment reruns
    scroll_top_flag = st.session_state.get("do_scroll_top", False)
    if scroll_top_flag:
        st.session_state.do_scroll_top = False

    ipp = st.session_state.get("grid_items_per_page", 50)
    total_pages = max(1, (len(review_data) + ipp - 1) // ipp)
    if st.session_state.get("grid_page", 0) >= total_pages:
        st.session_state.grid_page = 0

    current_page = st.session_state.get("grid_page", 0)
    if "last_iframe_selections" in st.session_state and current_page not in st.session_state.grid_page_selections:
        st.session_state.grid_page_selections[current_page] = st.session_state.get("last_iframe_selections", {})

    _last_known = st.session_state.get("_grid_page_last_known", None)
    _current_page = st.session_state.grid_page
    if _last_known != _current_page:
        st.session_state["_jump_top_raw"] = _current_page + 1
        st.session_state["_jump_bot_raw"] = _current_page + 1
        st.session_state["_grid_page_last_known"] = _current_page

    pg_cols = st.columns([1, 2, 1], vertical_alignment="center", gap="small")
    with pg_cols[0]:
        if st.button(
            "Prev Page",
            key="prev_top",
            icon=":material/arrow_back:",
            icon_position="left",
            width='stretch',
            disabled=st.session_state.get("grid_page", 0) == 0,
        ):
            if "last_iframe_selections" in st.session_state:
                st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                
            st.session_state.grid_page = max(
                0, st.session_state.get("grid_page", 0) - 1
            )
            st.session_state["_jump_top_raw"] = st.session_state.grid_page + 1
            st.session_state["_jump_bot_raw"] = st.session_state.grid_page + 1
            st.session_state["_grid_page_last_known"] = st.session_state.grid_page
            st.session_state.do_scroll_top = True
            st.rerun(scope="fragment")
    with pg_cols[1]:
        def _on_jump_top():
            v = st.session_state.get("_jump_top_raw", st.session_state.grid_page + 1)
            target = int(v) - 1
            if target != st.session_state.grid_page:
                if "last_iframe_selections" in st.session_state:
                    st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                st.session_state.grid_page = max(0, min(target, total_pages - 1))
                st.session_state["_jump_bot_raw"] = st.session_state.grid_page + 1
                st.session_state["_grid_page_last_known"] = st.session_state.grid_page
                st.session_state.do_scroll_top = True
        st.number_input(
            f"Jump to Page (Total: {total_pages} | {len(review_data)} items)",
            min_value=1,
            max_value=max(1, total_pages),
            step=1,
            key="_jump_top_raw",
            on_change=_on_jump_top,
        )
    with pg_cols[2]:
        if st.button(
            "Next Page",
            key="next_top",
            icon=":material/arrow_forward:",
            icon_position="right",
            width='stretch',
            disabled=st.session_state.grid_page >= total_pages - 1,
        ):
            if "last_iframe_selections" in st.session_state:
                st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                
            st.session_state.grid_page += 1
            st.session_state["_jump_top_raw"] = st.session_state.grid_page + 1
            st.session_state["_jump_bot_raw"] = st.session_state.grid_page + 1
            st.session_state["_grid_page_last_known"] = st.session_state.grid_page
            st.session_state.do_scroll_top = True
            st.rerun(scope="fragment")

    page_start = st.session_state.grid_page * ipp
    page_data = review_data.iloc[page_start : page_start + ipp]

    page_warnings = {}
    for _sid in page_data["PRODUCT_SET_SID"].astype(str):
        _warns = []
        _row_fr = fr[fr["ProductSetSid"].astype(str) == _sid]
        if not _row_fr.empty:
            _flag = _row_fr.iloc[0]["FLAG"]
            if _flag and _flag not in ("Approved",):
                _warns.append(_flag)
        _zip_index = st.session_state.get("_zip_sid_index")
        if _zip_index is not None and _sid in _zip_index.index:
            _zrow = _zip_index.loc[_sid]
            if hasattr(_zrow, "iloc") and hasattr(_zrow, "shape") and len(_zrow.shape) == 2:
                _zrow = _zrow.iloc[0]
            _zip_status_cols = st.session_state.get("_zip_status_cols", [])
            _zip_prefetch_map = st.session_state.get("_zip_prefetch_map", {})
            for _zcol in _zip_status_cols:
                _val = str(_zrow.get(_zcol, "")).lower()
                if _val == "rejected":
                    _zflag = _zip_prefetch_map.get(
                        _zcol, _zcol.replace("_Status", "").replace("_", " ").title()
                    )
                    if _zflag not in _warns:
                        _warns.append(_zflag)
                elif _val == "review":
                    _zflag = _zip_prefetch_map.get(
                        _zcol, _zcol.replace("_Status", "").replace("_", " ").title()
                    )
                    _rev_warn = f"{_zflag} - M"
                    if _rev_warn not in _warns:
                        _warns.append(_rev_warn)
        if _warns:
            page_warnings[_sid] = list(dict.fromkeys(_warns))

    seller_trust = {}
    if not fr.empty and "SELLER_NAME" in fr.columns:
        _stats = (
            fr.groupby("SELLER_NAME")["Status"]
            .value_counts(normalize=True)
            .unstack()
            .fillna(0)
        )
        if "Rejected" in _stats.columns:
            seller_trust = (_stats["Rejected"] * 100).round(1).to_dict()

    _prefetch_cache_key = f"prefetch_{st.session_state.grid_page}_{len(review_data)}"
    if _prefetch_cache_key not in st.session_state:
        prefetch_urls = []
        _already_warm = set(st.session_state.get("_grid_warm_urls", []))
        seen_urls = set(_already_warm)
        for prefetch_page in [
            st.session_state.grid_page + 1,
            st.session_state.grid_page + 2,
            st.session_state.grid_page + 3,
        ]:
            if prefetch_page >= total_pages:
                break
            p_start = prefetch_page * ipp
            for url in review_data.iloc[p_start : p_start + ipp]["MAIN_IMAGE"].astype(str):
                url = url.strip().replace("http://", "https://", 1)
                if url.startswith("https") and url not in seen_urls:
                    seen_urls.add(url)
                    prefetch_urls.append(url)
        st.session_state[_prefetch_cache_key] = prefetch_urls
    else:
        prefetch_urls = st.session_state[_prefetch_cache_key]

    rejected_state: dict = {}
    for _sid_raw in page_data["PRODUCT_SET_SID"].astype(str):
        _sid = _sid_raw.strip()
        if _sid in brand_image_check_sids:
            _row_fr = fr[fr["ProductSetSid"].astype(str).str.strip() == _sid]
            if not _row_fr.empty:
                _flag = str(_row_fr.iloc[0]["FLAG"])
                _comment = str(_row_fr.iloc[0]["Comment"])
                rejected_state[_sid] = _comment if _comment and _comment.lower() not in (
                    "nan", "none", "", "rejected"
                ) else _flag
            else:
                rejected_state[_sid] = "Brand Image Check"

    cols_per_row = 5
    
    current_page_num = st.session_state.grid_page
    previous_selections = st.session_state.grid_page_selections.get(current_page_num, {})
    import json
    selections_json = json.dumps(previous_selections)
    current_search = st.session_state.get("grid_search_term", "")
    
    grid_html = build_fast_grid_html(
        page_data=page_data,
        flags_mapping=support_files.get("flags_mapping", {}),
        country=st.session_state.get("selected_country", "Kenya"),
        page_warnings=page_warnings,
        rejected_state=rejected_state,
        cols_per_row=cols_per_row,
        poor_img_sids=brand_image_check_sids,
        prefetch_urls=prefetch_urls,
        scroll_to_top=scroll_top_flag,
        show_images=st.session_state.get("show_images", True),
        seller_trust=seller_trust,
        support_files=support_files,
        initial_sort=st.session_state.get("grid_sort", ""),
        initial_filter=st.session_state.get("grid_filter", ""),
        all_matching_sids=list(review_data["PRODUCT_SET_SID"].astype(str)),
        initial_search=current_search,
        initial_selections_json=selections_json
    )

    items_on_page = len(page_data)
    rows_needed = (items_on_page + cols_per_row - 1) // cols_per_row
    dynamic_height = max(800, (rows_needed * 380) + 250)
    
    components.html(grid_html, height=dynamic_height, scrolling=False)

    st.markdown("---")

    pg_cols_bot = st.columns([1, 2, 1, 1], vertical_alignment="center", gap="small")
    with pg_cols_bot[0]:
        if st.button(
            "Prev Page",
            key="prev_bot",
            icon=":material/arrow_back:",
            icon_position="left",
            width='stretch',
            disabled=st.session_state.get("grid_page", 0) == 0,
        ):
            if "last_iframe_selections" in st.session_state:
                st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                
            st.session_state.grid_page = max(
                0, st.session_state.get("grid_page", 0) - 1
            )
            st.session_state["_jump_top_raw"] = st.session_state.grid_page + 1
            st.session_state["_jump_bot_raw"] = st.session_state.grid_page + 1
            st.session_state["_grid_page_last_known"] = st.session_state.grid_page
            st.session_state.do_scroll_top = True
            st.rerun(scope="fragment")
    with pg_cols_bot[1]:
        def _on_jump_bot():
            v = st.session_state.get("_jump_bot_raw", st.session_state.grid_page + 1)
            target = int(v) - 1
            if target != st.session_state.grid_page:
                if "last_iframe_selections" in st.session_state:
                    st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                st.session_state.grid_page = max(0, min(target, total_pages - 1))
                st.session_state["_jump_top_raw"] = st.session_state.grid_page + 1
                st.session_state["_grid_page_last_known"] = st.session_state.grid_page
                st.session_state.do_scroll_top = True
        st.number_input(
            f"Jump to Page (Total: {total_pages} | {len(review_data)} items)",
            min_value=1,
            max_value=max(1, total_pages),
            step=1,
            key="_jump_bot_raw",
            on_change=_on_jump_bot,
        )
    with pg_cols_bot[2]:
        if st.button(
            "Next Page",
            key="next_bot",
            icon=":material/arrow_forward:",
            icon_position="right",
            width='stretch',
            disabled=st.session_state.grid_page >= total_pages - 1,
        ):
            if "last_iframe_selections" in st.session_state:
                st.session_state.grid_page_selections[st.session_state.grid_page] = st.session_state.get("last_iframe_selections", {})
                
            st.session_state.grid_page += 1
            st.session_state["_jump_top_raw"] = st.session_state.grid_page + 1
            st.session_state["_jump_bot_raw"] = st.session_state.grid_page + 1
            st.session_state["_grid_page_last_known"] = st.session_state.grid_page
            st.session_state.do_scroll_top = True
            st.rerun(scope="fragment")
    with pg_cols_bot[3]:
        if st.button(
            "Close Review", key="close_bot", width='stretch', type="secondary"
        ):
            st.session_state.show_review_modal = False
            st.rerun()

@st.dialog(
    "Visual Review Mode", width="large", icon=":material/pageview:", dismissible=False
)
def visual_review_modal(support_files):

    fr = st.session_state.final_report
    data = st.session_state.get("all_data_map", pd.DataFrame())

    _zip_qc = st.session_state.get("zip_qc_results", pd.DataFrame())
    _sid_col_qc = st.session_state.get("_zip_sid_col")
    if not _sid_col_qc and not _zip_qc.empty:
        for _possible in (
            "PRODUCT_SET_SID", "ProductSetSid", "Product Set SID",
            "cod_productset_sid", "SID",
        ):
            if _possible in _zip_qc.columns:
                _sid_col_qc = _possible
                break

    approved_mask = fr["Status"] == "Approved"

    brand_image_zip_mask = (
        (fr["Is_Zip"] == True)
        & (fr["FLAG"].str.contains("Brand Image Check", na=False, case=False))
    )

    manual_review_sids: set = set()
    if not _zip_qc.empty and _sid_col_qc:
        _mr_col = next(
            (c for c in _zip_qc.columns if c.lower() == "manual_review"),
            None,
        )
        if _mr_col:
            manual_review_sids = set(
                _zip_qc[
                    _zip_qc[_mr_col].astype(str).str.lower().isin(["true", "1", "yes"])
                ][_sid_col_qc].astype(str).str.strip().unique()
            )

    manual_review_mask = (
        fr["ProductSetSid"].astype(str).str.strip().isin(manual_review_sids)
    )

    valid_grid_df = fr[approved_mask | brand_image_zip_mask | manual_review_mask]

    brand_image_check_sids = set(
        fr[
            fr["FLAG"].str.contains("Brand Image Check", na=False, case=False)
            | fr["Comment"].str.contains("Brand Image Check", na=False, case=False)
        ]["ProductSetSid"].astype(str).str.strip().unique()
    )

    c1, c2, c3, c4 = st.columns(
        [1.5, 1.5, 1.5, 0.8], gap="large", vertical_alignment="bottom"
    )
    with c1:
        search_n = st.text_input(
            "Search by Name", placeholder="Product name…", icon=":material/search:",
            key="grid_search_n",
        )
    with c2:
        search_sc = st.text_input(
            "Search by Seller/Category",
            placeholder="Seller or Category…",
            icon=":material/store:",
            key="grid_search_sc",
        )
    with c3:
        st.session_state.grid_items_per_page = st.select_slider(
            "Items per page",
            options=[20, 50, 100, 200],
            value=st.session_state.get("grid_items_per_page", 50),
        )
    with c4:
        if st.button("Close", width='stretch', type="secondary"):
            st.session_state.show_review_modal = False
            st.rerun()

    _n_approved = int(approved_mask.sum())
    _n_brand = int(brand_image_zip_mask.sum())
    _n_manual = len(manual_review_sids)
    st.caption(
        f"Showing: **{_n_approved}** approved · "
        f"**{_n_brand}** Brand Image Check (ZIP) · "
        f"**{_n_manual}** Manual Review (ZIP)"
    )

    if "MAIN_IMAGE" not in data.columns:
        data["MAIN_IMAGE"] = ""

    _cached_review = st.session_state.get("_grid_review_data_cache")
    _cache_valid = (
        _cached_review is not None
        and _zip_qc.empty
        and not brand_image_check_sids
        and not manual_review_sids
        and len(_cached_review) > 0
    )
    if _cache_valid:
        review_data = _cached_review.copy()
    else:
        available_cols = [c for c in GRID_COLS if c in data.columns]
        if "CATEGORY" in data.columns and "CATEGORY" not in available_cols:
            available_cols.append("CATEGORY")
        if "CATEGORY_CODE" in data.columns and "CATEGORY_CODE" not in available_cols:
            available_cols.append("CATEGORY_CODE")
        if "IMAGE1_ZIP" in data.columns:
            available_cols.append("IMAGE1_ZIP")
        if "Brand_Detected_On_Product" in data.columns:
            available_cols.append("Brand_Detected_On_Product")
        for col in ["Duplicate_Flag", "Duplicate_flag", "Color_AI_Normalized", "Brand_Detected_On_Product", "brand_detected_on_product", "Detected_Brand", "BRAND"]:
            if col in data.columns and col not in available_cols:
                available_cols.append(col)
                
        v_df = valid_grid_df[["ProductSetSid", "Is_Zip", "FLAG", "Comment"]].copy()
        v_df["ProductSetSid"] = v_df["ProductSetSid"].astype(str).str.strip()
        d_df = data[available_cols].copy()
        d_df["PRODUCT_SET_SID"] = d_df["PRODUCT_SET_SID"].astype(str).str.strip()
        
        review_data = pd.merge(
            v_df,
            d_df,
            left_on="ProductSetSid",
            right_on="PRODUCT_SET_SID",
            how="left",
        )
        _code_to_path = support_files.get("code_to_path", {})
        if _code_to_path and "CATEGORY_CODE" in review_data.columns:
            def _resolve_cat_grid(row):
                existing = str(row.get("CATEGORY", "")).strip()
                if existing and existing.lower() != "nan": return existing
                code = clean_category_code(row.get("CATEGORY_CODE", ""))
                return _code_to_path.get(code, code)
            review_data["CATEGORY"] = review_data.apply(_resolve_cat_grid, axis=1)

    _curr_ctx = (search_n or "", search_sc or "", st.session_state.get("grid_items_per_page", 50))
    _prev_ctx = st.session_state.get("_grid_last_ctx", ("", "", 50))
    if _curr_ctx != _prev_ctx:
        st.session_state.grid_page = 0
        st.session_state["_grid_last_ctx"] = _curr_ctx

    if search_n:
        terms = [t.strip() for t in search_n.split(",") if t.strip()]
        if terms:
            name_mask = pd.Series(False, index=review_data.index)
            for t in terms:
                name_mask |= review_data["NAME"].astype(str).str.contains(t, case=False, na=False)
                if "BRAND" in review_data.columns:
                    name_mask |= review_data["BRAND"].astype(str).str.contains(t, case=False, na=False)
            review_data = review_data[name_mask]

    if search_sc:
        terms_sc = [t.strip() for t in search_sc.split(",") if t.strip()]
        if terms_sc:
            sc_mask = pd.Series(False, index=review_data.index)
            for t in terms_sc:
                if "CATEGORY" in review_data.columns:
                    sc_mask |= review_data["CATEGORY"].astype(str).str.contains(t, case=False, na=False)
                if "SELLER_NAME" in review_data.columns:
                    sc_mask |= review_data["SELLER_NAME"].astype(str).str.contains(t, case=False, na=False)
            review_data = review_data[sc_mask]

    _zip_index = st.session_state.get("_zip_sid_index")
    _zip_status_cols = st.session_state.get("_zip_status_cols", [])
    _zip_prefetch_map = st.session_state.get("_zip_prefetch_map", {})

    warnings_list = []
    is_dup_list = []
    color_mismatch_list = []

    for _, row in review_data.iterrows():
        sid = str(row["ProductSetSid"]).strip()
        warns = []
        
        row_fr = fr[fr["ProductSetSid"].astype(str).str.strip() == sid]
        if not row_fr.empty:
            flag = row_fr.iloc[0]["FLAG"]
            if flag and flag not in ("Approved", "Approved by User"):
                warns.append(flag)
                
        if _zip_index is not None and sid in _zip_index.index:
            zrow = _zip_index.loc[sid]
            if hasattr(zrow, "iloc") and hasattr(zrow, "shape") and len(zrow.shape) == 2:
                zrow = zrow.iloc[0]
            for zcol in _zip_status_cols:
                val = str(zrow.get(zcol, "")).lower()
                if val == "rejected":
                    zflag = _zip_prefetch_map.get(
                        zcol, zcol.replace("_Status", "").replace("_", " ").title()
                    )
                    if zflag not in warns:
                        warns.append(zflag)
                elif val == "review":
                    zflag = _zip_prefetch_map.get(
                        zcol, zcol.replace("_Status", "").replace("_", " ").title()
                    )
                    rev_warn = f"{zflag} - M"
                    if rev_warn not in warns:
                        warns.append(rev_warn)
                        
        dup_raw = str(row.get("Duplicate_Flag", str(row.get("Duplicate_flag", "")))).strip()
        is_duplicate = dup_raw.lower() not in ("", "nan", "none", "false")
        if is_duplicate and "Duplicate product" not in warns:
            warns.append("Duplicate product")
            
        color_val = str(row.get("COLOR", "")).strip()
        if color_val.lower() in ("nan", "none", "null"): color_val = ""
        color_ai = str(row.get("Color_AI_Normalized", "")).strip()
        if color_ai.lower() in ("nan", "none", "null", ""): color_ai = ""
        color_mismatch = False
        if color_ai and color_val:
            _ai_n, _dec_n = color_ai.lower().replace(" ", ""), color_val.lower().replace(" ", "")
            if _ai_n != _dec_n and _ai_n not in _dec_n and _dec_n not in _ai_n:
                color_mismatch = True
        elif color_ai and not color_val:
            color_mismatch = True
            
        warnings_list.append(warns)
        is_dup_list.append(is_duplicate)
        color_mismatch_list.append(color_mismatch)

    review_data["_warnings"] = warnings_list
    review_data["_is_duplicate"] = is_dup_list
    review_data["_color_mismatch"] = color_mismatch_list

    grid_filter = st.session_state.get("grid_filter", "")
    if grid_filter:
        CLIENT_IMAGE_FILTERS = ['Poor images', 'Low Resolution', 'Tall (Screenshot?)', 'Wide Aspect', 'Broken Image']
        if grid_filter not in CLIENT_IMAGE_FILTERS:
            if grid_filter == "brand_ocr":
                review_data = review_data[review_data["ProductSetSid"].astype(str).str.strip().isin(brand_image_check_sids)]
            elif grid_filter == "duplicates":
                review_data = review_data[review_data["_is_duplicate"] == True]
            elif grid_filter == "manual_review":
                review_data = review_data[review_data["ProductSetSid"].astype(str).str.strip().isin(manual_review_sids)]
            elif grid_filter == "color_mismatch":
                review_data = review_data[review_data["_color_mismatch"] == True]
            elif grid_filter == "committed":
                rej_sids = set(fr[fr["Status"] == "Rejected"]["ProductSetSid"].astype(str).str.strip().unique())
                review_data = review_data[review_data["ProductSetSid"].astype(str).str.strip().isin(rej_sids)]
            elif grid_filter == "no_flags":
                rej_sids = set(fr[fr["Status"] == "Rejected"]["ProductSetSid"].astype(str).str.strip().unique())
                review_data = review_data[
                    (review_data["_warnings"].apply(len) == 0) &
                    (~review_data["ProductSetSid"].astype(str).str.strip().isin(rej_sids))
                ]
            else:
                f_norm = grid_filter.replace("_", " ").lower()
                def _filter_match(row):
                    sid = str(row["ProductSetSid"]).strip()
                    if any(f_norm in w.lower() for w in row["_warnings"]):
                        return True
                    row_fr = fr[fr["ProductSetSid"].astype(str).str.strip() == sid]
                    if not row_fr.empty and row_fr.iloc[0]["Status"] == "Rejected":
                        reason = str(row_fr.iloc[0].get("Reason", "")).replace("_", " ").lower()
                        cmt = str(row_fr.iloc[0].get("Comment", "")).replace("_", " ").lower()
                        flag = str(row_fr.iloc[0].get("FLAG", "")).replace("_", " ").lower()
                        if f_norm in reason or f_norm in cmt or f_norm in flag:
                            return True
                    return False
                    
                review_data = review_data[review_data.apply(_filter_match, axis=1)]

    grid_sort = st.session_state.get("grid_sort", "")
    CLIENT_IMAGE_SORTS = ['low_res', 'tall', 'wide', 'broken', 'no_issue', 'most_flagged']
    if grid_sort and grid_sort not in CLIENT_IMAGE_SORTS:
        f_norm = grid_sort.replace("_", " ").lower()
        def _has_target_issue(row):
            sid = str(row["ProductSetSid"]).strip()
            if any(f_norm in w.lower() for w in row["_warnings"]):
                return 0
            row_fr = fr[fr["ProductSetSid"].astype(str).str.strip() == sid]
            if not row_fr.empty and row_fr.iloc[0]["Status"] == "Rejected":
                reason = str(row_fr.iloc[0].get("Reason", "")).replace("_", " ").lower()
                cmt = str(row_fr.iloc[0].get("Comment", "")).replace("_", " ").lower()
                flag = str(row_fr.iloc[0].get("FLAG", "")).replace("_", " ").lower()
                if f_norm in reason or f_norm in cmt or f_norm in flag:
                    return 0
            return 1
            
        review_data["_sort_key"] = review_data.apply(_has_target_issue, axis=1)
        review_data = review_data.sort_values(by=["_sort_key", "SELLER_NAME", "NAME"], ascending=[True, True, True]).drop(columns=["_sort_key"])
    elif grid_sort == "no_issue":
        review_data["_sort_key"] = review_data["_warnings"].apply(len)
        review_data = review_data.sort_values(by=["_sort_key", "SELLER_NAME", "NAME"], ascending=[True, True, True]).drop(columns=["_sort_key"])
    elif grid_sort == "most_flagged":
        review_data["_sort_key"] = review_data["_warnings"].apply(len)
        review_data = review_data.sort_values(by=["_sort_key", "SELLER_NAME", "NAME"], ascending=[False, True, True]).drop(columns=["_sort_key"])
    else:
        review_data = review_data.sort_values(by=["SELLER_NAME", "NAME"], na_position="last").reset_index(drop=True)

    st.session_state["_vr_data"] = review_data
    _visual_grid_fragment(support_files, brand_image_check_sids, fr)

@st.fragment
def render_image_grid(support_files):
    if (
        st.session_state.final_report.empty
        or st.session_state.get("file_mode") == "post_qc"
    ):
        return

    st.markdown("---")

    _warm_urls = st.session_state.get("_grid_warm_urls", [])
    if _warm_urls:
        _preload_tags = "\n".join(
            f'<link rel="preload" as="image" href="{url}" referrerpolicy="no-referrer">'
            for url in _warm_urls[:10]
        )
        st.markdown(
            f"<div style='display:none'>{_preload_tags}</div>", unsafe_allow_html=True
        )

    c1, c2 = st.columns([3, 1], gap="medium")
    with c1:
        st.header(_t("manual_review"), anchor=False)
        st.caption("Open Focus Mode to rapidly visually review and reject products.")
    with c2:
        if st.button("Start Visual Review", type="primary", width='stretch'):
            st.session_state.show_review_modal = True

    if st.session_state.get("show_review_modal", False):
        visual_review_modal(support_files)

def _render_export_card(title, df, desc, func, exports_config):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown(
            f'<div style="height: 65px; overflow: hidden; font-size: 0.85rem; color: #6b7a8d; margin-bottom: 10px;">{desc}</div>',
            unsafe_allow_html=True,
        )
        st.metric(label="Rows", value=f"{len(df):,}")
        if title not in st.session_state.exports_cache:
            if st.button(
                "Generate",
                key=f"gen_{title}",
                type="primary",
                width='stretch',
                icon=":material/download:",
                icon_position="left",
            ):
                with st.spinner("Generating all reports…"):
                    for t2, d2, _, f2 in exports_config:
                        if t2 not in st.session_state.exports_cache:
                            res, fname, mime = f2(d2)
                            st.session_state.exports_cache[t2] = {
                                "data": res.getvalue(),
                                "fname": fname,
                                "mime": mime,
                            }
                st.rerun()
        else:
            cache = st.session_state.exports_cache[title]
            st.download_button(
                "Download",
                data=cache["data"],
                file_name=cache["fname"],
                mime=cache["mime"],
                width='stretch',
                type="primary",
                icon=":material/file_download:",
                key=f"dl_{title}",
            )
            if st.button("Clear", key=f"clr_{title}", width='stretch'):
                del st.session_state.exports_cache[title]
                st.rerun()

@st.fragment
def render_exports_section(support_files, country_validator):
    if (
        st.session_state.final_report.empty
        or st.session_state.get("file_mode") == "post_qc"
    ):
        return

    from datetime import datetime

    fr = st.session_state.final_report
    data = st.session_state.get("all_data_map", pd.DataFrame())

    if st.session_state.get("all_data_rows") is None:
        if "_data_filtered_ref" in st.session_state:
            st.session_state.all_data_rows = st.session_state._data_filtered_ref
        elif "current_sig_hash" in st.session_state:
            _fname = f"{st.session_state.current_sig_hash}_data_rows.parquet"
            st.session_state.all_data_rows = load_df_parquet(_fname)

    all_rows = st.session_state.get("all_data_rows", data)
    app_df = fr[fr["Status"] == "Approved"]
    rej_df = fr[fr["Status"] == "Rejected"]
    c_code = st.session_state.get("selected_country", "Kenya")[:2].upper()
    date_str = datetime.now().strftime("%Y-%m-%d")
    reasons_df = support_files.get("reasons", pd.DataFrame())

    st.markdown("---")
    st.header(_t("download_reports"), anchor=False)
    st.caption("Export QC results in Excel or ZIP format")

    exports_config = [
        (
            "QC Export",
            fr,
            "Complete QC report with all statuses",
            lambda df: generate_smart_export(
                df, f"{c_code}_QC_Export_{date_str}", "simple", reasons_df
            ),
        ),
        (
            "Rejected Only",
            rej_df,
            "Products that failed QC validation",
            lambda df: generate_smart_export(
                df, f"{c_code}_Rejected_{date_str}", "simple", reasons_df
            ),
        ),
        (
            "Approved Only",
            app_df,
            "Products that passed QC validation",
            lambda df: generate_smart_export(
                df, f"{c_code}_Approved_{date_str}", "simple", reasons_df
            ),
        ),
        (
            "Full Data",
            data,
            "Complete dataset with QC flags for every processed row",
            lambda df: generate_smart_export(
                prepare_full_data_merged(df, fr), f"{c_code}_Full_{date_str}", "full"
            ),
        ),
    ]

    all_cached = all(
        t in st.session_state.exports_cache for t, _, _, _ in exports_config
    )
    if all_cached:
        st.success("All reports generated and ready to download.")
    else:
        if st.button("Generate All Reports", type="primary", width='stretch'):
            with st.spinner("Generating all reports…"):
                for t2, d2, _, f2 in exports_config:
                    if t2 not in st.session_state.exports_cache:
                        res, fname, mime = f2(d2)
                        st.session_state.exports_cache[t2] = {
                            "data": res.getvalue(),
                            "fname": fname,
                            "mime": mime,
                        }
            st.rerun()

    cols_count = 4 if st.session_state.get("layout_mode") == "wide" else 2
    for i in range(0, len(exports_config), cols_count):
        cols = st.columns(cols_count)
        for j, col in enumerate(cols):
            if i + j < len(exports_config):
                title, df, desc, func = exports_config[i + j]
                with col:
                    _render_export_card(title, df, desc, func, exports_config)