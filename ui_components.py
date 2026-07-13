"""
ui_components.py - All Streamlit UI rendering components, dialogs, and the image grid
"""

import base64
import concurrent.futures
import gc
import html as html_lib
import json
import logging
import re
import zipfile
from io import BytesIO

import orjson
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from constants import GRID_COLS, JUMIA_COLORS
from data_utils import (
    _get_image_from_zip,
    clean_category_code,
    df_hash,
    format_local_price,
    load_df_parquet,
)
from export_utils import generate_smart_export, prepare_full_data_merged
from targeted_audit import targeted_audit_modal

logger = logging.getLogger(__name__)

# Securely encoded Base64 placeholder (No Image fallback)
_SVG_RAW = "<svg xmlns='http://www.w3.org/2000/svg' width='150' height='150'><rect width='150' height='150' fill='#f0f0f0'/><text x='75' y='75' text-anchor='middle' dominant-baseline='central' font-size='12' font-family='sans-serif' fill='#999'>No Image</text></svg>"
_NO_IMAGE_SVG = f"data:image/svg+xml;base64,{base64.b64encode(_SVG_RAW.encode('utf-8')).decode('utf-8')}"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


PREFETCH_DISPLAY_COLUMNS = {
    "Wrong Category": [
        "Category_Check_Status",
        "Category_Check_Rejection_Reason",
        "Initial_Category_Path",
        "Suggested_Categories",
        "Top1_Category",
        "AI_Product_Caption",
        "Category_Match_Score",
        "Top1_Score",
    ],
    "Product Warranty": [
        "Warranty_Check_Status",
        "Warranty_Rejection_Reason",
        "product_warranty",
        "warranty_duration",
        "warranty_type",
        "warranty_address",
    ],
    "Missing COLOR": [
        "Color_Check_Status",
        "Color_Rejection_Reason",
        "Color_AI_Normalized",
        "color",
        "color_family",
    ],
    "Wrong Variation": [
        "Variation_Check_Status",
        "Variation_Rejection_Reason",
        "count_variations",
        "list_variations",
        "COUNT_VARIATIONS",
        "LIST_VARIATIONS",
    ],
    "BRAND name repeated in NAME": [
        "Brand_Image_Check_Status",
        "Brand_Image_Check_Reason",
        "Brand_Detected_On_Product",
        "Product Name_Brand Name_Status",
        "Product name_Brand name_rejection reason",
        "Product_Name_Brand_Name_Status",
        "Product Name_Brand Name_Rejection_Reason",
    ],
    "Poor images": [
        "Image_Quality_Check_Status",
        "Image_Quality_Check_Reason",
        "Image_Extraction_Status",
        "Image_Filename",
    ],
    "Missing Weight/Volume": [
        "Title_Language_Check_Status",
        "Title_Language_Check_Reason",
    ],
    "Duplicate product": ["Duplicate_Flag"],
    "FDA": ["FDA_Check_Status", "FDA_Rejection_Reason", "FDA"],
    "Category Check": [
        "Category_Check_Status",
        "Category_Check_Rejection_Reason",
        "Initial_Category_Path",
        "Suggested_Categories",
        "Top1_Category",
        "AI_Product_Caption",
        "Category_Match_Score",
        "Top1_Score",
    ],
    "Warranty Check": [
        "Warranty_Check_Status",
        "Warranty_Rejection_Reason",
        "product_warranty",
        "warranty_duration",
        "warranty_type",
        "warranty_address",
    ],
    "Color Check": [
        "Color_Check_Status",
        "Color_Rejection_Reason",
        "Color_AI_Normalized",
        "color",
    ],
    "Variation Check": [
        "Variation_Check_Status",
        "Variation_Rejection_Reason",
        "count_variations",
        "list_variations",
        "COUNT_VARIATIONS",
        "LIST_VARIATIONS",
    ],
    "Brand Image Check": [
        "Brand_Image_Check_Status",
        "Brand_Image_Check_Reason",
        "Brand_Detected_On_Product",
    ],
    "Product Name Brand Name": [
        "Product Name_Brand Name_Status",
        "Product name_Brand name_rejection reason",
        "Product Name_Brand Name_Rejection_Reason",
    ],
    "Title Language Check": [
        "Title_Language_Check_Status",
        "Title_Language_Check_Reason",
    ],
    "Image Quality Check": [
        "Image_Quality_Check_Status",
        "Image_Quality_Check_Reason",
        "Image_Extraction_Status",
        "Image_Filename",
    ],
}


def flag_pill_header(flag_name: str, count: int, is_zip: bool = False) -> str:
    color_map = {
        "Wrong Category": ("#fef3c7", "#d97706"),
        "Restricted brands": ("#fee2e2", "#dc2626"),
        "Suspected Fake product": ("#fee2e2", "#b91c1c"),
        "BRAND name repeated in NAME": ("#ede9fe", "#7c3aed"),
        "Duplicate product": ("#dcfce7", "#15803d"),
    }
    bg, fg = color_map.get(flag_name, ("#f3f4f6", "#374151"))

    if is_zip:
        bg, fg = ("#f8fafc", "#334155")

    zip_badge = (
        ' <span style="background:linear-gradient(135deg, #3b82f6, #1d4ed8);color:white;border-radius:6px;padding:2px 8px;font-size:10px;font-weight:900;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-left:8px;">ZIP</span>'
        if is_zip
        else ""
    )

    return (
        f'<div style="display:flex;align-items:center;padding:10px 0;">'
        f'<span style="background:{fg};color:white;border-radius:8px;'
        f'padding:4px 12px;font-size:14px;font-weight:900;box-shadow:0 4px 12px {bg};">{count}</span>'
        f'<span style="font-size:16px;font-weight:700;margin-left:12px;color:#1f2937;">{flag_name}</span>'
        f'{zip_badge}</div>'
    )


def render_kpi_bar(final_report: pd.DataFrame):
    total = len(final_report)
    approved = int((final_report["Status"] == "Approved").sum())
    rejected = int((final_report["Status"] == "Rejected").sum())
    zip_rej = (
        int((final_report["Is_Zip"] == True).sum())
        if "Is_Zip" in final_report.columns
        else 0
    )
    pct = round(approved / total * 100, 1) if total else 0
    trend_color = "#22c55e" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"

    st.html(
        f"""
    <style>
      .kpi-strip {{
        display:grid; grid-template-columns:repeat(4,1fr);
        gap:12px; margin-bottom:8px;
      }}
      .kpi-card {{
        background:#fff; border:1px solid #e5e7eb;
        border-radius:12px; padding:16px 20px;
        box-shadow:0 1px 3px rgba(0,0,0,.06);
        transition:transform .15s,box-shadow .15s;
      }}
      .kpi-card:hover {{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.10);}}
      .kpi-label {{font-size:11px;font-weight:600;color:#6b7280;letter-spacing:.06em;text-transform:uppercase;}}
      .kpi-value {{font-size:28px;font-weight:800;margin:4px 0;}}
      .kpi-sub   {{font-size:11px;color:#9ca3af;}}
      .kpi-bar   {{height:4px;border-radius:99px;margin-top:10px;background:#f3f4f6;}}
      .kpi-fill  {{height:4px;border-radius:99px;background:{trend_color};
                   width:{pct}%;transition:width .8s cubic-bezier(.4,0,.2,1);}}
    </style>
    <div class="kpi-strip">
      <div class="kpi-card">
        <div class="kpi-label">Total SKUs</div>
        <div class="kpi-value" style="color:#111827">{total:,}</div>
        <div class="kpi-sub">Unique product sets</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Approved</div>
        <div class="kpi-value" style="color:#16a34a">{approved:,}</div>
        <div class="kpi-sub">{pct}% approval rate</div>
        <div class="kpi-bar"><div class="kpi-fill"></div></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Rejected</div>
        <div class="kpi-value" style="color:#dc2626">{rejected:,}</div>
        <div class="kpi-sub">{zip_rej} from ZIP/prefetch</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Approval Rate</div>
        <div class="kpi-value" style="color:{trend_color}">{pct}%</div>
        <div class="kpi-sub">{"Good" if pct>=70 else "Review needed" if pct>=40 else "High rejection"}</div>
        <div class="kpi-bar"><div class="kpi-fill"></div></div>
      </div>
    </div>
    """
    )


def render_summary_header(final_report: pd.DataFrame):
    render_kpi_bar(final_report)


def render_rejection_donut(final_report: pd.DataFrame):
    import plotly.graph_objects as go
    import plotly.express as px

    rej = final_report[final_report["Status"] == "Rejected"]
    if rej.empty:
        return
    counts = rej["FLAG"].str.replace(r" \(Prefetched\)", "", regex=True).value_counts()
    fig = go.Figure(
        go.Pie(
            labels=counts.index,
            values=counts.values,
            hole=0.55,
            textinfo="label+percent",
            textfont_size=11,
            marker=dict(
                colors=px.colors.qualitative.Pastel,
                line=dict(color="#fff", width=2),
            ),
            hovertemplate="<b>%{label}</b><br>%{value} SKUs (%{percent})<extra></extra>",
        )
    )
    fig.update_layout(
        annotations=[
            dict(
                text=f"<b>{len(rej)}</b><br>rejected",
                x=0.5,
                y=0.5,
                font_size=14,
                showarrow=False,
            )
        ],
        showlegend=False,
        margin=dict(t=0, b=0, l=0, r=0),
        height=240,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


def _base_prefetched_title(title: str) -> str:
    return str(title).replace("(Prefetched)", "").strip()


def _clean_reason_value(value) -> str:
    val = str(value).strip()
    return (
        ""
        if val.lower() in ("", "nan", "none", "null", "rejected", "approved", "skipped")
        else val
    )


def _prefetched_reason_for_row(title: str, row, fallback="No reason provided") -> str:
    base_title = _base_prefetched_title(title)
    for col in PREFETCH_DISPLAY_COLUMNS.get(base_title, []):
        if col in row.index:
            val = _clean_reason_value(row.get(col))
            if val:
                return val
    for col in row.index:
        col_l = str(col).lower()
        if "reason" in col_l and col_l != "reason":
            val = _clean_reason_value(row.get(col))
            if val:
                return val
    return fallback


def _t(key):
    from translations import get_translation

    return get_translation(st.session_state.get("ui_lang", "en"), key)


def _clear_flag_df_selection(title: str):
    ver_key = f"df_ver_{title}"
    st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1


def _normalize_sid_set(sids) -> set:
    return {str(s).strip() for s in sids if str(s).strip()}


def _clear_result_caches() -> None:
    st.session_state.exports_cache.clear()
    st.session_state.display_df_cache.clear()
    st.session_state.pop("_grid_review_data_cache", None)
    st.session_state.pop("_grid_warm_urls", None)
    gc.collect()


def _drop_sids_from_post_qc_results(sid_set: set) -> None:
    results = st.session_state.get("post_qc_results", {})
    if not isinstance(results, dict) or not sid_set:
        return
    for flag, df in list(results.items()):
        if (
            not isinstance(df, pd.DataFrame)
            or df.empty
            or "PRODUCT_SET_SID" not in df.columns
        ):
            continue
        mask = df["PRODUCT_SET_SID"].astype(str).str.strip().isin(sid_set)
        if mask.any():
            results[flag] = df.loc[~mask].copy()


def _add_sids_to_post_qc_results(sid_set: set, flag: str, comment: str = "") -> None:
    if not sid_set or not flag:
        return
    base_flag = str(flag).replace("(Prefetched)", "").strip()
    data = st.session_state.get("all_data_map", pd.DataFrame())
    if (
        not isinstance(data, pd.DataFrame)
        or data.empty
        or "PRODUCT_SET_SID" not in data.columns
    ):
        return
    base_rows = data[
        data["PRODUCT_SET_SID"].astype(str).str.strip().isin(sid_set)
    ].copy()
    if base_rows.empty:
        return
    base_rows["Comment_Detail"] = comment
    results = st.session_state.setdefault("post_qc_results", {})
    existing = results.get(base_flag)
    if isinstance(existing, pd.DataFrame) and not existing.empty:
        combined = pd.concat([existing, base_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset=["PRODUCT_SET_SID"], keep="last")
        results[base_flag] = combined
    else:
        results[base_flag] = base_rows


def apply_status_change(
    sids,
    *,
    status: str,
    reason: str = "",
    comment: str = "",
    flag: str = "",
    is_manual: bool = True,
    is_zip: bool = False,
    sync_quick_rejects: bool = True,
) -> int:
    sid_set = _normalize_sid_set(sids)

    is_image_rej = status == "Rejected" and any(x in str(flag).lower() for x in ["image", "stretched", "blurry", "poor", "mismatch"])
    if is_image_rej:
        all_data = st.session_state.get("all_data_map")
        if all_data is not None and "PRODUCT_SET_SID" in all_data.columns and "IMAGE1" in all_data.columns:
            _input_sids_clean = list(sid_set)
            _target_images = set(
                all_data[all_data["PRODUCT_SET_SID"].astype(str).str.strip().isin(_input_sids_clean)]["IMAGE1"]
                .dropna().unique()
            )
            if _target_images:
                _global_similar_sids = set(
                    all_data[all_data["IMAGE1"].isin(_target_images)]["PRODUCT_SET_SID"]
                    .astype(str).str.strip().unique()
                )
                sid_set.update(_global_similar_sids)

    fr = st.session_state.get("final_report", pd.DataFrame())
    if (
        not sid_set
        or not isinstance(fr, pd.DataFrame)
        or fr.empty
        or "ProductSetSid" not in fr.columns
    ):
        return 0

    mask = fr["ProductSetSid"].astype(str).str.strip().isin(sid_set)
    if not mask.any():
        return 0

    from datetime import datetime

    st.session_state["undo_snapshot"] = {
        "final_report": fr.copy(),
        "timestamp": datetime.now(),
    }

    fr.loc[mask, ["Status", "Reason", "Comment", "FLAG", "Is_Manual", "Is_Zip"]] = [
        status,
        reason,
        comment,
        flag,
        is_manual,
        is_zip,
    ]

    _drop_sids_from_post_qc_results(sid_set)
    if status == "Rejected" and flag:
        _add_sids_to_post_qc_results(sid_set, flag, comment)

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

    if len(sid_set) > 1:
        st.session_state["show_undo_toast"] = {
            "count": len(sid_set),
            "status": status,
            "time": datetime.now(),
        }

    return int(mask.sum())


@st.dialog("Confirm Bulk Approval", icon=":material/check_circle:")
def bulk_approve_dialog(
    sids_to_process,
    title,
    subset_data,
    data_has_warranty_cols_check,
    support_files,
    country_validator,
    validation_runner,
):
    try:
        from category_matcher_engine import get_engine

        _CAT_MATCHER_AVAILABLE = True
    except ImportError:
        _CAT_MATCHER_AVAILABLE = False

    st.warning(
        f"You are about to approve **{len(sids_to_process)}** items from `{title}`."
    )
    _preview_cols = [
        c
        for c in ["PRODUCT_SET_SID", "NAME", "BRAND", "SELLER_NAME"]
        if c in subset_data.columns
    ]
    _preview_df = subset_data[subset_data["PRODUCT_SET_SID"].isin(sids_to_process)][
        _preview_cols
    ].reset_index(drop=True)
    with st.expander(
        f"Preview {len(_preview_df)} item(s) to be approved",
        expanded=len(_preview_df) <= 10,
    ):
        st.dataframe(_preview_df, hide_index=True, width='stretch')
    if st.button(_t("approve_btn"), type="primary", width='stretch'):
        with st.spinner("Validating…"):
            _progress = st.progress(0, text="Running validation…")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _executor:
                data_hash = (
                    df_hash(subset_data) + country_validator.code + "_skip_" + title
                )
                _future = _executor.submit(
                    validation_runner,
                    data_hash,
                    subset_data,
                    support_files,
                    country_validator.code,
                    data_has_warranty_cols_check,
                    [title],
                )
                import time as _time

                _elapsed = 0
                while not _future.done():
                    _time.sleep(0.1)
                    _elapsed += 0.1
                    _progress.progress(
                        min(0.9, _elapsed / 10), text="Running validation…"
                    )
                _future.result()
            _progress.progress(1.0, text="Done!")
            _progress.empty()
            msg_moved = {}
            sids_str = [str(sid).strip() for sid in sids_to_process]
            msg_approved = apply_status_change(
                sids_str,
                status="Approved",
                reason="",
                comment="",
                flag="Approved by User",
                is_manual=True,
                is_zip=False,
            )

            if msg_approved > 0:
                st.toast(
                    f"Approved {msg_approved} product(s) successfully!",
                    icon=":material/check_circle:",
                )
            if msg_moved:
                for f, c in msg_moved.items():
                    st.toast(
                        f"{c} product(s) moved to '{f}' (other issues found)",
                        icon=":material/note:",
                    )

            if title == "Wrong Category" and _CAT_MATCHER_AVAILABLE:
                try:
                    engine = get_engine()
                    if engine is not None:
                        learned = 0
                        for sid in sids_to_process:
                            row = subset_data[
                                subset_data["PRODUCT_SET_SID"].astype(str).str.strip()
                                == str(sid)
                            ]
                            if row.empty:
                                continue
                            name = str(row.iloc[0].get("NAME", "")).strip()
                            if not name:
                                continue
                            engine.set_compiled_rules(
                                st.session_state.get("compiled_json_rules", {})
                            )
                            predicted = engine.get_category_with_boost(name)
                            if predicted and predicted.lower() not in (
                                "nan",
                                "none",
                                "uncategorized",
                                "",
                            ):
                                engine.apply_learned_correction(
                                    name, predicted, auto_save=False
                                )
                                learned += 1
                        if learned:
                            engine.save_learning_db()
                            st.session_state.main_toasts.append(
                                f"Engine learned {learned} correction(s) from your approvals."
                            )
                except Exception as _le:
                    logger.warning("Wrong Category approval learning failed: %s", _le)

            if msg_approved > 0:
                st.session_state.main_toasts.append(
                    f"{msg_approved} items successfully Approved!"
                )
            for flag, count in msg_moved.items():
                st.session_state.main_toasts.append(
                    f"{count} items re-flagged as: {flag}"
                )

            st.session_state[f"exp_{title}"] = True
            _clear_flag_df_selection(title)
        st.rerun()


@st.fragment
def render_flag_expander(
    title,
    df_flagged_sids,
    data,
    data_has_warranty_cols_check,
    support_files,
    country_validator,
    validation_runner,
):
    try:
        from category_matcher_engine import get_engine

        _CAT_MATCHER_AVAILABLE = True
    except ImportError:
        _CAT_MATCHER_AVAILABLE = False

    cache_key = f"display_df_{title}_{df_hash(data)}_prefetch_context_v3"
    base_display_cols = [
        "PRODUCT_SET_SID",
        "NAME",
        "BRAND",
        "CATEGORY",
        "COLOR",
        "GLOBAL_SALE_PRICE",
        "GLOBAL_PRICE",
        "PARENTSKU",
        "SELLER_NAME",
    ]
    current_display_cols = base_display_cols.copy()
    for col in PREFETCH_DISPLAY_COLUMNS.get(_base_prefetched_title(title), []):
        if col not in current_display_cols:
            current_display_cols.append(col)
    if title == "Wrong Variation":
        for col in ("COUNT_VARIATIONS", "LIST_VARIATIONS"):
            if col in data.columns:
                current_display_cols.append(col)

    if title == "Category Max Price Exceeded":
        current_display_cols.append("CAT_MAX_PRICE")

    if title == "Wrong Category":
        current_display_cols.append("AI Suggested Category")

    possible_img_cols = [
        "image1",
        "MAIN_IMAGE_URL",
        "MAIN_IMAGE",
        "IMAGE_URL",
        "IMAGE1_ZIP",
    ]
    img_col = next((c for c in possible_img_cols if c in data.columns), None)
    if img_col and img_col not in current_display_cols:
        current_display_cols.append(img_col)

    if cache_key not in st.session_state.display_df_cache:
        _extra_cols = [c for c in current_display_cols if c in data.columns]
        if "CATEGORY_CODE" in data.columns and "CATEGORY_CODE" not in _extra_cols:
            _extra_cols.append("CATEGORY_CODE")

        if "PRODUCT_SET_SID" not in _extra_cols:
            _extra_cols.append("PRODUCT_SET_SID")

        if "Is_Zip" not in df_flagged_sids.columns:
            df_flagged_sids = df_flagged_sids.copy()
            df_flagged_sids["Is_Zip"] = False
        if "Is_Manual" not in df_flagged_sids.columns:
            df_flagged_sids = (
                df_flagged_sids.copy()
                if "Is_Zip" in df_flagged_sids.columns
                else df_flagged_sids
            )
            df_flagged_sids["Is_Manual"] = False
        df_display = pd.merge(
            df_flagged_sids[["ProductSetSid", "Is_Zip"]],
            data,
            left_on="ProductSetSid",
            right_on="PRODUCT_SET_SID",
            how="left",
        )
        _extra_cols_cleaned = [c for c in _extra_cols if c in df_display.columns]
        if "IMAGE1_ZIP" in df_display.columns:
            _extra_cols_cleaned.append("IMAGE1_ZIP")

        df_display = df_display[list(dict.fromkeys(_extra_cols_cleaned + ["Is_Zip"]))]

        if (
            title == "Category Max Price Exceeded"
            and "CAT_MAX_PRICE" in df_flagged_sids.columns
        ):
            _cap_map = df_flagged_sids.set_index("ProductSetSid")[
                "CAT_MAX_PRICE"
            ].to_dict()
            sid_col = (
                "PRODUCT_SET_SID"
                if "PRODUCT_SET_SID" in df_display.columns
                else "ProductSetSid"
            )
            df_display["CAT_MAX_PRICE"] = df_display[sid_col].map(_cap_map)

        if (
            title == "Wrong Category"
            and "Suggested_Category" in df_flagged_sids.columns
        ):
            _sug_map = df_flagged_sids.set_index("ProductSetSid")[
                "Suggested_Category"
            ].to_dict()
            sid_col = (
                "PRODUCT_SET_SID"
                if "PRODUCT_SET_SID" in df_display.columns
                else "ProductSetSid"
            )
            df_display["AI Suggested Category"] = df_display[sid_col].map(_sug_map)

        _code_to_path = support_files.get("code_to_path", {})
        if _code_to_path and "CATEGORY_CODE" in df_display.columns:
            df_display["CATEGORY"] = df_display["CATEGORY_CODE"].apply(
                lambda c: _code_to_path.get(str(c).strip(), "") if pd.notna(c) else ""
            )
            df_display = df_display.drop(columns=["CATEGORY_CODE"])
        _final_cols = list(
            dict.fromkeys(
                [c for c in current_display_cols if c in df_display.columns]
                + ["Is_Zip"]
            )
        )
        df_display = df_display[_final_cols]
        st.session_state.display_df_cache[cache_key] = df_display
    else:
        df_display = st.session_state.display_df_cache[cache_key]

    show_table_images = st.toggle(
        "Show Image Previews",
        value=st.session_state.get("show_table_images", False),
        key=f"tg_img_{title}",
    )
    st.session_state.show_table_images = show_table_images

    c1, c2, c3 = st.columns([1, 1, 1], gap="large")
    with c1:
        search_term = st.text_input(
            _t("search_grid"),
            placeholder="Name, Brand...",
            icon=":material/search:",
            key=f"s_{title}",
        )
    with c2:
        _seller_key = f"f_{title}"
        _seller_options = sorted(df_display["SELLER_NAME"].astype(str).unique()) if "SELLER_NAME" in df_display.columns else []
        seller_filter = st.multiselect(
            "Filter by Seller",
            _seller_options,
            default=[s for s in st.session_state.get(f"_sf_{title}", []) if s in _seller_options],
            key=_seller_key,
        )
        st.session_state[f"_sf_{title}"] = seller_filter
    with c3:
        _cat_key = f"fc_{title}"
        _cat_options = sorted(df_display["CATEGORY"].dropna().astype(str).unique()) if "CATEGORY" in df_display.columns else []
        _cat_default = [c for c in st.session_state.get(f"_cf_{title}", []) if c in _cat_options]
        category_filter = st.multiselect("Filter by Category", _cat_options, default=_cat_default, key=_cat_key)
        st.session_state[f"_cf_{title}"] = category_filter

    df_view = df_display.copy()
    if search_term:
        _search_cols = [
            c for c in ["NAME", "BRAND", "SELLER_NAME"] if c in df_view.columns
        ]
        if _search_cols:
            mask = (
                df_view[_search_cols]
                .apply(
                    lambda col: col.astype(str).str.contains(
                        search_term, case=False, na=False
                    )
                )
                .any(axis=1)
            )
            df_view = df_view[mask]
    if seller_filter:
        df_view = df_view[df_view["SELLER_NAME"].isin(seller_filter)]
    if category_filter and "CATEGORY" in df_view.columns:
        df_view = df_view[df_view["CATEGORY"].astype(str).isin(category_filter)]
    if "CATEGORY" in df_view.columns:
        df_view = df_view.sort_values("CATEGORY", na_position="last")
    df_view = df_view.reset_index(drop=True)

    if "NAME" in df_view.columns:
        df_view["NAME"] = df_view["NAME"].apply(
            lambda t: re.sub("<[^<]+?>", "", t) if isinstance(t, str) else t
        )

    def get_img(row):
        if not img_col or img_col not in row:
            return None
        sid = row.get("PRODUCT_SET_SID")
        name = row.get("NAME", "")
        brand = row.get("BRAND", "")
        img_val = row.get(img_col, "")
        if pd.isna(img_val):
            img_val = ""
        zip_img = _get_image_from_zip(name, brand, img_val)
        if zip_img:
            return zip_img
        if (
            "IMAGE1_ZIP" in row
            and pd.notna(row["IMAGE1_ZIP"])
            and str(row["IMAGE1_ZIP"]).startswith("http")
        ):
            return str(row["IMAGE1_ZIP"])
        if str(img_val).startswith("http"):
            return str(img_val).replace("http://", "https://", 1)
        return None
    if "GLOBAL_PRICE" in df_view.columns and "GLOBAL_SALE_PRICE" in df_view.columns:

        def _local_p(row):
            sp, rp = row.get("GLOBAL_SALE_PRICE"), row.get("GLOBAL_PRICE")
            val = sp if pd.notna(sp) and str(sp).strip() != "" else rp
            return format_local_price(val, country_validator.country)

        df_view.insert(
            df_view.columns.get_loc("GLOBAL_PRICE") + 1
            if "GLOBAL_PRICE" in df_view.columns
            else len(df_view.columns),
            "Local Price",
            df_view.apply(_local_p, axis=1),
        )

    def style_rows(row):
        if row.get("Is_Zip"):
            return ["color: #ff4b4b; font-weight: 900;"] * len(row)
        return [""] * len(row)

    df_styled = df_view.style.apply(style_rows, axis=1)

    _filters_active = bool(search_term or seller_filter)
    sel_all_col, sel_clear_col, _sel_spacer = st.columns([1, 1, 3])
    with sel_all_col:
        _sel_all_label = f"Select All ({len(df_view)} filtered)" if _filters_active else f"Select All ({len(df_view)})"
        if st.button(_sel_all_label, key=f"selall_{title}", disabled=df_view.empty, help="Select every row currently shown below"):
            st.session_state[f"df_{title}_{st.session_state.get(f'df_ver_{title}', 0)}"] = {"selection": {"rows": list(range(len(df_view)))}}
    with sel_clear_col:
        if st.button("Clear Selection", key=f"selclear_{title}"):
            st.session_state[f"df_{title}_{st.session_state.get(f'df_ver_{title}', 0)}"] = {"selection": {"rows": []}}


    _df_key = f"df_{title}_{st.session_state.get(f'df_ver_{title}', 0)}"
    df_kwargs = {
        "hide_index": True,
        "width": "stretch",
        "selection_mode": "multi-row",
        "on_select": "rerun",
        "key": _df_key,
    }
    if len(df_view) <= 2:
        df_kwargs["height"] = 150

    _col_cfg = {
            "PRODUCT_SET_SID": st.column_config.TextColumn(pinned=True),
            "NAME": st.column_config.TextColumn(pinned=True),
            "PARENTSKU": st.column_config.TextColumn(),
            "SELLER_SKU": st.column_config.TextColumn(),
            "SID": st.column_config.TextColumn(),
            "CATEGORY": st.column_config.TextColumn("Full Category", width="large"),
            "GLOBAL_SALE_PRICE": st.column_config.NumberColumn(
                "Sale Price (USD)", format="$%.2f"
            ),
            "GLOBAL_PRICE": st.column_config.NumberColumn(
                "Price (USD)", format="$%.2f"
            ),
            "Local Price": st.column_config.TextColumn(
                f"Local Price ({country_validator.country})"
            ),
            "CAT_MAX_PRICE": st.column_config.TextColumn(
                "Category Max Price",
                help="Maximum allowed price for this category in local currency",
            ),
            "AI Suggested Category": st.column_config.TextColumn(
                "AI Suggestion",
                width="large",
                help="AI predicted correct category path",
            ),
            "Is_Zip": None,
        }

    # Wire up the Show Image Previews toggle
    if show_table_images and img_col:
        df_view.insert(0, "_Image_Preview", df_view.apply(get_img, axis=1))
        _col_cfg["_Image_Preview"] = st.column_config.ImageColumn("Preview", width="small")
    elif "_Image_Preview" in df_view.columns:
        df_view = df_view.drop(columns=["_Image_Preview"])

    df_styled = df_view.style.apply(style_rows, axis=1)

    event = st.dataframe(
        df_styled,
        **df_kwargs,
        column_config=_col_cfg,
    )

    raw_selected = list(event.selection.rows)
    selected_indices = [i for i in raw_selected if i < len(df_view)]
    has_selection = len(selected_indices) > 0
    _sel_color = JUMIA_COLORS["primary_orange"] if has_selection else "#aaa"
    st.markdown(
        f"<div style='display:inline-block;background:{_sel_color};color:#fff;"
        f"padding:4px 14px;border-radius:9999px;font-size:13px;font-weight:700;"
        f"margin-bottom:8px;'>"
        f"{len(selected_indices)} / {len(df_view)} selected</div>",
        unsafe_allow_html=True,
    )

    _fm = support_files["flags_mapping"]
    _reason_options = [
        "Wrong Category", "Restricted brands", "Suspected Fake product", "Seller Not approved to sell Refurb",
        "Product Warranty", "Seller Approve to sell books", "Seller Approved to Sell Perfume", "Counterfeit Sneakers",
        "Suspected counterfeit Jerseys", "Prohibited products", "Unnecessary words in NAME", "Single-word NAME",
        "Generic BRAND Issues", "Fashion brand issues", "BRAND name repeated in NAME", "Wrong Variation",
        "Generic branded products with genuine brands", "Missing COLOR", "Missing Weight/Volume", "Incomplete Smartphone Name",
        "Duplicate product", "Poor images", "Image Stretched", "Image Blurry", "Image Mismatch",
        "Image Infringing", "Image Too Many things displayed", "Perfume Tester", "NG - Gift Card Seller",
        "NG - Books Seller", "NG - TV Brand Seller", "NG - HP Toners Seller", "NG - Apple Seller",
        "NG - Xmas Tree Seller", "NG - Rice Brand Seller", "NG - Powerbank Capacity", "Discount too high",
        "Category Max Price Exceeded", "Suspicious Discount", "Color Mismatch", "FDA", "Category Check", "Warranty Check",
        "Color Check", "Variation Check", "Brand Image Check", "Title Language Check", "Image Quality Check",
        "Product Name Brand Name", "Other Reason (Custom)",
    ]

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button(
            _t("approve_btn"),
            key=f"approve_sel_{title}",
            type="primary",
            width='stretch',
            disabled=not has_selection,
        ):
            sids_to_process = df_view.iloc[selected_indices]["PRODUCT_SET_SID"].tolist()
            subset = data[data["PRODUCT_SET_SID"].isin(sids_to_process)]
            _clear_flag_df_selection(title)
            bulk_approve_dialog(
                sids_to_process,
                title,
                subset,
                data_has_warranty_cols_check,
                support_files,
                country_validator,
                validation_runner,
            )

    with btn_col2:
        pop_ver = st.session_state.get(f"pop_ver_{title}", 0)
        popover_key = f"popover_rej_{title}_{pop_ver}"
        with st.popover(
            _t("reject_as"),
            width='stretch',
            disabled=not has_selection,
            key=popover_key,
        ):
            chosen_reason = st.selectbox(
                "Reason",
                _reason_options,
                key=f"rej_reason_dd_{title}",
                label_visibility="collapsed",
            )
            _cmt_lang = (
                "fr" if st.session_state.get("selected_country") == "Morocco" else "en"
            )

            if chosen_reason == "Other Reason (Custom)":
                custom_comment = st.text_area(
                    "Custom comment",
                    placeholder="Type your rejection reason here...",
                    key=f"custom_comment_{title}",
                    height=80,
                )
                if st.button(
                    "Apply",
                    key=f"apply_custom_{title}",
                    type="primary",
                    width='stretch',
                    disabled=not has_selection,
                ):
                    to_reject = df_view.iloc[selected_indices][
                        "PRODUCT_SET_SID"
                    ].tolist()
                    final_comment = (
                        custom_comment.strip()
                        if custom_comment.strip()
                        else "Other Reason"
                    )
                    apply_status_change(
                        to_reject,
                        status="Rejected",
                        reason="1000007 - Other Reason",
                        comment=final_comment,
                        flag="Other Reason (Custom)",
                        is_manual=True,
                        is_zip=False,
                    )
                    st.session_state.main_toasts.append(
                        f"{len(to_reject)} items rejected with custom reason."
                    )
                    st.session_state[f"exp_{title}"] = True
                    _clear_flag_df_selection(title)
                    st.session_state[f"pop_ver_{title}"] = pop_ver + 1
                    st.rerun()
            else:
                _rinfo = _fm.get(
                    chosen_reason,
                    {"reason": "1000007 - Other Reason", "en": chosen_reason},
                )
                _rcode = _rinfo["reason"]
                _rcmt = _rinfo.get(_cmt_lang, _rinfo.get("en"))
                st.info(f"**Seller message:** {_rcmt}", icon=":material/chat:")
                if st.button(
                    "Apply",
                    key=f"apply_dd_{title}",
                    type="primary",
                    width='stretch',
                    disabled=not has_selection,
                ):
                    to_reject = df_view.iloc[selected_indices][
                        "PRODUCT_SET_SID"
                    ].tolist()
                    apply_status_change(
                        to_reject,
                        status="Rejected",
                        reason=_rcode,
                        comment=_rcmt,
                        flag=chosen_reason,
                        is_manual=True,
                        is_zip=False,
                    )
                    st.session_state.main_toasts.append(
                        f"{len(to_reject)} items rejected as '{chosen_reason}'."
                    )

                    if (
                        chosen_reason == "Wrong Category"
                        and title != "Wrong Category"
                        and _CAT_MATCHER_AVAILABLE
                    ):
                        try:
                            engine = get_engine()
                            _cats = support_files.get("categories_names_list", [])
                            if engine is not None and _cats:
                                if not engine._tfidf_built:
                                    engine.build_tfidf_index(_cats)
                                learned = 0
                                for sid in to_reject:
                                    prod_row = data[
                                        data["PRODUCT_SET_SID"].astype(str).str.strip()
                                        == str(sid)
                                    ]
                                    if prod_row.empty:
                                        continue
                                    name = str(prod_row.iloc[0].get("NAME", "")).strip()
                                    if not name:
                                        continue
                                    engine.set_compiled_rules(
                                        st.session_state.get("compiled_json_rules", {})
                                    )
                                    predicted = engine.get_category_with_boost(name)
                                    if predicted and predicted.lower() not in (
                                        "nan",
                                        "none",
                                        "uncategorized",
                                        "",
                                    ):
                                        engine.apply_learned_correction(
                                            name, predicted, auto_save=False
                                        )
                                        learned += 1
                                if learned:
                                    engine.save_learning_db()
                                    st.session_state.main_toasts.append(
                                        f"Engine noted {learned} missed Wrong Category item(s)."
                                    )
                        except Exception as _le:
                            logger.warning(
                                "Wrong Category manual rejection learning failed: %s",
                                _le,
                            )

                    st.session_state[f"exp_{title}"] = True
                    _clear_flag_df_selection(title)
                    st.session_state[f"pop_ver_{title}"] = pop_ver + 1
                    st.rerun()


def build_fast_grid_html(
    page_data,
    flags_mapping,
    country,
    page_warnings,
    rejected_state,
    cols_per_row,
    poor_img_sids=None,
    prefetch_urls=None,
    scroll_to_top=False,
    show_images=True,
    seller_trust=None,
    support_files=None,
    curr_sort="",
    curr_flag="",
):
    if seller_trust is None: seller_trust = {}
    if support_files is None: support_files = {}

    from translations import get_translation
    lang = st.session_state.get("ui_lang", "en")

    def _t(key): return get_translation(lang, key)

    O = JUMIA_COLORS["primary_orange"]
    G = JUMIA_COLORS["success_green"]
    R = JUMIA_COLORS["jumia_red"]
    def _js_json(v):
        return orjson.dumps(v).decode("utf-8").replace("</", "<\\/")

    committed_json = _js_json(rejected_state)
    poor_img_sids_json = _js_json(list(poor_img_sids or []))
    prefetch_json = _js_json(prefetch_urls or [])
    html_dir = "rtl" if st.session_state.get("ui_lang") == "ar" else "ltr"

    labels_dict = {
        "poor_img":       _t("poor_img"),
        "img_stretched":  _t("img_stretched"),
        "img_blurry":     _t("img_blurry"),
        "img_mismatch":   _t("img_mismatch"),
        "img_infringing": _t("img_infringing"),
        "img_too_many":   _t("img_too_many"),
        "wrong_cat":      _t("wrong_cat"),
        "fake_prod":      _t("fake_prod"),
        "restr_brand":    _t("restr_brand"),
        "wrong_brand":    _t("wrong_brand"),
        "prohibited":     _t("prohibited"),
        "missing_color":  _t("missing_color"),
        "other_custom":   _t("other_custom"),
        "more_options":   _t("more_options"),
        "undo":           _t("undo"),
        "approve":        _t("approve_btn"),
        "clear_sel":      _t("clear_sel"),
        "items_pending":  _t("items_pending"),
        "batch_reject":   _t("batch_reject"),
        "select_all":     _t("select_all"),
        "deselect_all":   _t("deselect_all"),
        "rejected":       str(_t("rejected") or "REJECTED").upper(),
        "sort_by_issue":      _t("sort_by_issue"),
        "most_flagged":       _t("most_flagged"),
        "no_issue_first":     _t("no_issue_first"),
        "filter_by_flag":     _t("filter_by_flag"),
        "all_rejected":       _t("all_rejected"),
        "clean_no_flags":     _t("clean_no_flags"),
        "grp_image":          _t("grp_image"),
        "grp_qc_flags":       _t("grp_qc_flags"),
        "grp_prefetch":       _t("grp_prefetch"),
        "sort_low_res":       _t("sort_low_res"),
        "sort_tall":          _t("sort_tall"),
        "sort_wide":          _t("sort_wide"),
        "sort_broken":        _t("sort_broken"),
        "sort_wrong_cat":     _t("sort_wrong_cat"),
        "sort_restr_brand":   _t("sort_restr_brand"),
        "sort_fake":          _t("sort_fake"),
        "sort_missing_color": _t("sort_missing_color"),
        "sort_warranty":      _t("sort_warranty"),
        "sort_duplicates":    _t("sort_duplicates"),
        "filter_brand_ocr":   _t("filter_brand_ocr"),
        "filter_duplicates":  _t("filter_duplicates"),
        "filter_manual":      _t("filter_manual"),
        "filter_color_mis":   _t("filter_color_mis"),
        "filter_brand_name":  _t("filter_brand_name"),
        "filter_unneeded":    _t("filter_unneeded"),
        "filter_prohibited":  _t("filter_prohibited"),
        "custom_reason_title": _t("custom_reason_title"),
        "custom_reason_ph":    _t("custom_reason_ph"),
        "custom_apply":        _t("custom_apply"),
        "custom_cancel":       _t("custom_cancel"),
        "search_grid":     _t("search_grid"),
        "products_label":  _t("products_label"),
        "dark_mode":       _t("dark_mode"),
    }
    labels_json = _js_json(labels_dict)

    _PLACEHOLDER_SVG = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='180' viewBox='0 0 300 180'>"
        "<defs><linearGradient id='g' x1='0%' y1='0%' x2='100%' y2='100%'><stop offset='0%' stop-color='%23FFF8F2'/><stop offset='100%' stop-color='%23FFEFE5'/></linearGradient></defs>"
        "<rect width='300' height='180' rx='12' fill='url(%23g)'/>"
        "<text x='150' y='80' text-anchor='middle' font-family='sans-serif' font-size='34' "
        "font-weight='800' fill='%23FF8800' letter-spacing='-1'>JUMIA</text>"
        "<text x='150' y='110' text-anchor='middle' font-family='sans-serif' font-size='14' "
        "font-weight='600' fill='%23FF8800' opacity='0.7'>Loading...</text>"
        "</svg>"
    )

    _zip_img_cache: dict = {}

    _zip_index_ss = st.session_state.get("_zip_sid_index")
    _zip_sid_set = set()
    if _zip_index_ss is not None and not _zip_index_ss.empty:
        _zip_sid_set = set(_zip_index_ss.index.astype(str).tolist())
    _fr_ss = st.session_state.get("final_report", pd.DataFrame())
    if not _zip_sid_set:
        if not _fr_ss.empty and "Is_Zip" in _fr_ss.columns and "ProductSetSid" in _fr_ss.columns:
            _zip_sid_set = set(
                _fr_ss[_fr_ss["Is_Zip"] == True]["ProductSetSid"].astype(str).tolist()
            )

    _zip_override_map = {}
    if not _fr_ss.empty and "zip_override" in _fr_ss.columns and "ProductSetSid" in _fr_ss.columns:
        _zip_override_map = _fr_ss.set_index("ProductSetSid")["zip_override"].fillna("").to_dict()

    cards_data = []
    for _, row in page_data.iterrows():
        sid = str(row["PRODUCT_SET_SID"]).strip()
        img_url = str(row.get("MAIN_IMAGE", "")).strip()
        if img_url.startswith("http"):
            img_url = img_url.replace("http://", "https://", 1)
        elif img_url:
            name = str(row.get("NAME", "")).strip()
            brand = str(row.get("BRAND", "")).strip()
            _zip_cache_key = (name, brand, img_url)
            if _zip_cache_key not in _zip_img_cache:
                _zip_img_cache[_zip_cache_key] = _get_image_from_zip(name, brand, img_url)
            img_data = _zip_img_cache[_zip_cache_key]
            if img_data:
                img_url = img_data
            else:
                img_url = ""

        if (not img_url or img_url == "") and "IMAGE1_ZIP" in row:
            _fallback = str(row.get("IMAGE1_ZIP", "")).strip()
            if _fallback.startswith("http"):
                img_url = _fallback.replace("http://", "https://", 1)

        sale_p = row.get("GLOBAL_SALE_PRICE")
        reg_p = row.get("GLOBAL_PRICE")
        usd_val = sale_p if pd.notna(sale_p) and str(sale_p).strip() != "" else reg_p
        if pd.isna(usd_val) or str(usd_val).strip().lower() in ("", "nan", "none", "null"):
            price_str = "NaN"
        else:
            price_str = format_local_price(
                usd_val, st.session_state.get("selected_country", "Kenya")
            ) or "NaN"

        color_val = str(row.get("COLOR", "")).strip()
        if color_val.lower() in ("nan", "none", "null"):
            color_val = ""

        color_ai = str(row.get("Color_AI_Normalized", "")).strip()
        if color_ai.lower() in ("nan", "none", "null", ""):
            color_ai = ""
        if not color_ai and _zip_index_ss is not None and sid in _zip_index_ss.index:
            _zr = _zip_index_ss.loc[sid]
            if hasattr(_zr, "iloc") and len(getattr(_zr, "shape", (1,))) == 2:
                _zr = _zr.iloc[0]
            _zai = str(_zr.get("Color_AI_Normalized", "")).strip()
            if _zai.lower() not in ("nan", "none", "null", ""):
                color_ai = _zai
        color_mismatch = ""
        if color_ai and color_val:
            _ai_n = color_ai.lower().replace(" ", "")
            _dec_n = color_val.lower().replace(" ", "")
            if _ai_n != _dec_n and _ai_n not in _dec_n and _dec_n not in _ai_n:
                color_mismatch = f"AI: '{color_ai}' vs declared: '{color_val}'"
        elif color_ai and not color_val:
            color_mismatch = f"AI detected color: '{color_ai}' (none declared)"

        dup_raw = str(row.get("Duplicate_Flag", "")).strip()
        is_duplicate = dup_raw.lower() not in ("", "nan", "none", "false")

        mr_raw = str(row.get("Manual_Review", "")).strip().lower()
        qc_skip = str(row.get("QC_Skip_Reason", "")).strip()
        if qc_skip.lower() in ("nan", "none", "null", ""):
            qc_skip = ""

        is_manual_review = (
            mr_raw in ("true", "1", "yes") 
            or "Manual review" in page_warnings.get(sid, [])
            or bool(qc_skip)
        )

        cat_reason = str(row.get("Category_Check_Rejection_Reason", "")).strip()
        if cat_reason.lower() in ("nan", "none", "rejected", ""):
            cat_reason = ""
        suggested_cats_raw = str(row.get("Suggested_Categories", "")).strip()
        suggested_cat = ""
        if suggested_cats_raw and suggested_cats_raw.lower() not in ("nan", "none", ""):
            first_pipe = suggested_cats_raw.split("|")[0]
            suggested_cat = re.sub(r"\s*\(\d+%\)\s*$", "", first_pipe).strip()

        ai_caption = str(row.get("AI_Product_Caption", "")).strip()
        if ai_caption.lower() in ("nan", "none", ""):
            ai_caption = ""

        cards_data.append(
            {
                "sid": sid,
                "img": img_url if show_images else _PLACEHOLDER_SVG,
                "name": str(row.get("NAME", "")),
                "brand": str(row.get("BRAND", "Unknown Brand")),
                "cat": str(row.get("Initial_Category_Path", row.get("CATEGORY", "Unknown Category"))),
                "seller": str(row.get("SELLER_NAME", "Unknown Seller")),
                "color": color_val,
                "brand_detected": str(
                    row.get(
                        "Brand_Detected_On_Product",
                        row.get(
                            "brand_detected_on_product", row.get("Detected_Brand", "")
                        ),
                    )
                ).strip()
                if (
                    pd.notna(row.get("Brand_Detected_On_Product"))
                    and str(row.get("Brand_Detected_On_Product")).lower()
                    not in ("nan", "none")
                )
                or (
                    pd.notna(row.get("brand_detected_on_product"))
                    and str(row.get("brand_detected_on_product")).lower()
                    not in ("nan", "none")
                )
                else "",
                "warnings": page_warnings.get(sid, []),
                "price": price_str,
                "data_name": str(row.get("NAME", "")).replace('"', "&quot;"),
                "data_brand": str(row.get("BRAND", "")).replace('"', "&quot;"),
                "data_sid": sid,
                "data_cat": str(row.get("CATEGORY", "")).replace('"', "&quot;"),
                "is_duplicate": is_duplicate,
                "is_manual_review": is_manual_review,
                "qc_skip_reason": qc_skip,
                "color_mismatch": color_mismatch,
                "color_ai": color_ai,
                "cat_reason": cat_reason,
                "suggested_cat": suggested_cat,
                "ai_caption": ai_caption,
                "is_zip": sid in _zip_sid_set,
                "zip_override": str(_zip_override_map.get(sid, "")),
            }
        )

    cards_json = orjson.dumps(cards_data).decode("utf-8").replace("</", "<\\/")
    seller_opts = sorted(
        {
            str(card.get("seller", "")).strip()
            for card in cards_data
            if str(card.get("seller", "")).strip()
        }
    )
    category_opts = sorted(
        {
            str(card.get("cat", "")).strip()
            for card in cards_data
            if str(card.get("cat", "")).strip()
        }
    )
    seller_opts_json = _js_json(seller_opts)
    category_opts_json = _js_json(category_opts)

    scroll_js = ""
    if scroll_to_top:
        scroll_js = "sessionStorage.removeItem('__inner_iframe_scroll__'); window.scrollTo(0, 0);"
    else:
        scroll_js = """
        var savedInnerScroll = sessionStorage.getItem('__inner_iframe_scroll__');
        if (savedInnerScroll) {
            setTimeout(function() {
                window.scrollTo({top: parseInt(savedInnerScroll, 10), behavior: 'instant'});
            }, 50);
        }
        """


    def _sel(val, curr): return "selected" if val == curr else ""
    sort_html = f'''
  <select class="reason-sel sort-sel" id="sort-sel-top" onchange="sendMsg('grid_sort_issue', this.value)" style="max-width:170px;" title="{labels_dict.get('sort_by_issue', 'Sort')}">
    <option value="" {_sel('', curr_sort)}>{labels_dict.get('sort_by_issue', 'Sort')}</option>
    <option value="most_flagged" {_sel('most_flagged', curr_sort)}>{labels_dict.get('most_flagged', 'Most Flagged')}</option>
    <option value="no_issue" {_sel('no_issue', curr_sort)}>{labels_dict.get('no_issue_first', 'No Issue')}</option>
    <option disabled>── {labels_dict.get('grp_image', 'Image')} ──</option>
    <option value="low_res" {_sel('low_res', curr_sort)}>{labels_dict.get('sort_low_res', 'Low Res')}</option>
    <option value="tall" {_sel('tall', curr_sort)}>{labels_dict.get('sort_tall', 'Tall')}</option>
    <option value="wide" {_sel('wide', curr_sort)}>{labels_dict.get('sort_wide', 'Wide')}</option>
    <option value="broken" {_sel('broken', curr_sort)}>{labels_dict.get('sort_broken', 'Broken')}</option>
    <option disabled>── {labels_dict.get('grp_qc_flags', 'QC')} ──</option>
    <option value="Wrong Category" {_sel('Wrong Category', curr_sort)}>{labels_dict.get('sort_wrong_cat', 'Wrong Cat')}</option>
    <option value="Restricted brands" {_sel('Restricted brands', curr_sort)}>{labels_dict.get('sort_restr_brand', 'Restricted')}</option>
    <option value="Suspected Fake product" {_sel('Suspected Fake product', curr_sort)}>{labels_dict.get('sort_fake', 'Fake')}</option>
    <option value="Missing COLOR" {_sel('Missing COLOR', curr_sort)}>{labels_dict.get('sort_missing_color', 'Color')}</option>
    <option value="Product Warranty" {_sel('Product Warranty', curr_sort)}>{labels_dict.get('sort_warranty', 'Warranty')}</option>
    <option value="Duplicate product" {_sel('Duplicate product', curr_sort)}>{labels_dict.get('sort_duplicates', 'Duplicate')}</option>
    <option disabled>── {labels_dict.get('grp_prefetch', 'Prefetch')} ──</option>
    <option value="Category Check" {_sel('Category Check', curr_sort)}>Category Check</option>
    <option value="Warranty Check" {_sel('Warranty Check', curr_sort)}>Warranty Check</option>
    <option value="FDA" {_sel('FDA', curr_sort)}>FDA</option>
    <option value="Color Check" {_sel('Color Check', curr_sort)}>Color Check</option>
    <option value="Variation Check" {_sel('Variation Check', curr_sort)}>Variation Check</option>
    <option value="Brand Image Check" {_sel('Brand Image Check', curr_sort)}>Brand Image Check</option>
    <option value="Title Language Check" {_sel('Title Language Check', curr_sort)}>Title Language Check</option>
    <option value="Image Quality Check" {_sel('Image Quality Check', curr_sort)}>Image Quality Check</option>
  </select>
'''
    filter_html = f'''
  <select class="reason-sel sort-sel" id="filter-sel-top" onchange="sendMsg('grid_filter_flag', this.value)" style="max-width:180px;" title="{labels_dict.get('filter_by_flag', 'Filter')}">
    <option value="" {_sel('', curr_flag)}>{labels_dict.get('filter_by_flag', 'Filter')}</option>
    <option value="brand_ocr" {_sel('brand_ocr', curr_flag)}>{labels_dict.get('filter_brand_ocr', 'Brand OCR')}</option>
    <option value="duplicates" {_sel('duplicates', curr_flag)}>{labels_dict.get('filter_duplicates', 'Duplicates')}</option>
    <option value="manual_review" {_sel('manual_review', curr_flag)}>{labels_dict.get('filter_manual', 'Manual Review')}</option>
    <option value="color_mismatch" {_sel('color_mismatch', curr_flag)}>{labels_dict.get('filter_color_mis', 'Color Mis')}</option>
    <option value="committed" {_sel('committed', curr_flag)}>{labels_dict.get('all_rejected', 'All Rejected')}</option>
    <option value="no_flags" {_sel('no_flags', curr_flag)}>{labels_dict.get('clean_no_flags', 'Clean')}</option>
    <option disabled>── {labels_dict.get('grp_qc_flags', 'QC')} ──</option>
    <option value="Wrong Category" {_sel('Wrong Category', curr_flag)}>{labels_dict.get('sort_wrong_cat', 'Wrong Cat')}</option>
    <option value="Restricted brands" {_sel('Restricted brands', curr_flag)}>{labels_dict.get('sort_restr_brand', 'Restricted')}</option>
    <option value="Suspected Fake product" {_sel('Suspected Fake product', curr_flag)}>{labels_dict.get('sort_fake', 'Fake')}</option>
    <option value="Missing COLOR" {_sel('Missing COLOR', curr_flag)}>{labels_dict.get('sort_missing_color', 'Color')}</option>
    <option value="Product Warranty" {_sel('Product Warranty', curr_flag)}>{labels_dict.get('sort_warranty', 'Warranty')}</option>
    <option value="Duplicate product" {_sel('Duplicate product', curr_flag)}>{labels_dict.get('sort_duplicates', 'Duplicate')}</option>
    <option value="BRAND name repeated in NAME" {_sel('BRAND name repeated in NAME', curr_flag)}>{labels_dict.get('filter_brand_name', 'Brand Name')}</option>
    <option value="Unnecessary words" {_sel('Unnecessary words', curr_flag)}>{labels_dict.get('filter_unneeded', 'Unneeded')}</option>
    <option value="Prohibited Words" {_sel('Prohibited Words', curr_flag)}>{labels_dict.get('filter_prohibited', 'Prohibited')}</option>
    <option disabled>── {labels_dict.get('grp_prefetch', 'Prefetch')} ──</option>
    <option value="Category Check" {_sel('Category Check', curr_flag)}>Category Check</option>
    <option value="Warranty Check" {_sel('Warranty Check', curr_flag)}>Warranty Check</option>
    <option value="FDA" {_sel('FDA', curr_flag)}>FDA</option>
    <option value="Color Check" {_sel('Color Check', curr_flag)}>Color Check</option>
    <option value="Variation Check" {_sel('Variation Check', curr_flag)}>Variation Check</option>
    <option value="Brand Image Check" {_sel('Brand Image Check', curr_flag)}>Brand Image Check</option>
    <option value="Title Language Check" {_sel('Title Language Check', curr_flag)}>Title Language Check</option>
    <option value="Image Quality Check" {_sel('Image Quality Check', curr_flag)}>Image Quality Check</option>
    <option value="Product Name Brand Name" {_sel('Product Name Brand Name', curr_flag)}>Name/Brand Check</option>
  </select>
'''
    _cols_btns_parts = []
    for _n in [5, 6, 7]:
        _active = _n == cols_per_row
        _border = "var(--accent)" if _active else "var(--border)"
        _bg = "var(--accent)" if _active else "transparent"
        _color = "#fff" if _active else "var(--text)"
        _title = " title='Wide mode + 500/page'" if _n >= 6 else ""
        _cols_btns_parts.append(
            f'<button onclick="sendMsg(\'grid_cols_per_row\', {_n})"{_title} '
            f'style="padding:1px 7px;font-size:10px;font-weight:700;border-radius:4px;'
            f'border:1px solid {_border};background:{_bg};color:{_color};'
            f'cursor:pointer;line-height:1.6;">{_n}</button>'
        )
    _cols_btns = "".join(_cols_btns_parts)

    return f"""<!DOCTYPE html>
<html dir="{html_dir}">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<style>
  :root {{
    --bg: #f9fafb;
    --card: #ffffff;
    --text: #111827;
    --border: #e5e7eb;
    --accent: {O};
  }}
  *{{box-sizing:border-box;margin:0;padding:0;font-family:sans-serif;}}
  body{{background:var(--bg);color:var(--text);padding:8px 8px 80px 8px;overflow-x:hidden;width:100%;transition:background .2s, color .2s;}}

  .ctrl-bar{{position:-webkit-sticky;position:sticky;top:0;z-index:99999;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 12px;background:var(--card);backdrop-filter:blur(8px);border-bottom:2px solid var(--accent);border-radius:4px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.15);}}
  .ctrl-bar.top-bar{{flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.35) transparent;}}
  .ctrl-bar.top-bar::-webkit-scrollbar{{height:8px;}}
  .ctrl-bar.top-bar::-webkit-scrollbar-thumb{{background:rgba(0,0,0,.28);border-radius:999px;}}
  .ctrl-bar.top-bar::-webkit-scrollbar-track{{background:transparent;}}

  #grid-search {{
    flex: 1;
    min-width: 200px;
    padding: 8px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    font-size: 13px;
    outline: none;
    background: var(--bg);
    color: var(--text);
  }}
  .filter-group{{display:flex;flex-direction:column;gap:4px;min-width:180px;}}
  .filter-group .group-label{{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:11px;font-weight:800;color:var(--text);opacity:.86;white-space:nowrap;}}
  .filter-group .sel-count{{font-size:10px;font-weight:700;color:var(--accent);background:rgba(246,139,30,.12);padding:2px 6px;border-radius:999px;white-space:nowrap;}}
  .filter-group select[multiple]{{min-height:72px;padding:6px 10px;}}
  .filter-group select{{width:100%;}}
  .filter-group .hint{{font-size:10px;color:var(--text);opacity:.62;white-space:nowrap;}}
  .top-summary{{display:flex;flex-direction:column;gap:2px;min-width:180px;max-width:320px;}}
  .top-summary .main{{font-size:12px;font-weight:800;color:var(--text);white-space:nowrap;}}
  .top-summary .sub{{font-size:10px;color:var(--text);opacity:.72;white-space:nowrap;}}
  .toolbar-btn{{white-space:nowrap;}}
  .toolbar-btn.small{{padding:7px 10px;}}
  .page-nav{{display:flex;align-items:center;gap:6px;white-space:nowrap;}}
  .page-pill{{font-size:11px;font-weight:800;color:var(--text);opacity:.75;padding:0 4px;}}
  .empty-state{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;background:linear-gradient(135deg,rgba(246,139,30,.08),rgba(59,130,246,.06));border:1px dashed rgba(246,139,30,.35);border-radius:14px;margin:18px 0;}}
  .empty-state .title{{font-size:15px;font-weight:800;color:var(--text);margin-bottom:4px;}}
  .empty-state .desc{{font-size:12px;color:var(--text);opacity:.74;}}
  .empty-state .actions{{display:flex;gap:8px;flex-wrap:wrap;}}
  #dark-toggle {{
    padding: 6px 12px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--text);
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
  }}
  #dark-toggle:hover {{ background: #f3f4f6; }}

  .bottom-bar {{position: fixed; bottom: 26px; left: 0; width: 100%; top: auto; border-bottom: none; border-top: 1px solid rgba(246, 139, 30, 0.2); margin: 0; z-index: 10000; box-shadow: 0 -4px 16px rgba(0,0,0,0.1); background: var(--card); padding: 12px 16px;}}

  .sel-count{{font-weight:700;color:{O};font-size:13px;min-width:80px;}}
  .reason-sel{{flex:1;min-width:160px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:12px;background:#fff;cursor:pointer;}}
  .batch-btn{{padding:7px 14px;background:{O};color:#fff;border:none;border-radius:4px;font-weight:700;font-size:12px;cursor:pointer;}}
  .batch-btn:hover{{opacity:.88;}}
  .desel-btn{{padding:7px 12px;background:#fff;color:#555;border:1px solid #ccc;border-radius:4px;font-size:12px;cursor:pointer;}}
  .desel-btn:hover{{background:#f5f5f5;}}
  .top-btn {{margin-left: auto; background: #313133; color: white; border-color: #313133; font-weight: bold;}}
  .top-btn:hover {{background: #000; color: white;}}

  .grid{{display:grid;grid-template-columns:repeat({cols_per_row},minmax(0,1fr));gap:12px;width:100%;}}
  .card{{border:2px solid var(--border);border-radius:8px;padding:10px;background:var(--card);position:relative;transition:border-color .15s,box-shadow .15s,transform .2s;z-index:1;min-width:0;word-wrap:break-word;display:flex;flex-direction:column;min-height:360px;outline:none;}}
  .card:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(246,139,30,0.3); transform: translateY(-4px); }}

  .card.selected{{border-color:{O};box-shadow:0 0 0 5px rgba(255,136,0,.35);background:rgba(255,136,0,.04);}}
  .card.staged-rej{{border-color:{R};box-shadow:0 0 0 4px rgba(231,60,23,.3);background:rgba(231,60,23,.04);}}
  .card.committed-rej{{border-color:#bbb;opacity:.6;}}
  .card.manual-review{{border-color:#dc2626;box-shadow:0 0 0 3px rgba(220,38,38,0.25);}}
  .card.zip-card{{border-left:4px solid #3b82f6;box-shadow:-4px 0 8px rgba(59,130,246,0.20);}}
  .card.zip-card.selected{{border-left:4px solid #3b82f6;box-shadow:-4px 0 8px rgba(59,130,246,0.20),0 0 0 5px rgba(255,136,0,.35);}}
  .card.zip-card.manual-review{{border-color:#dc2626;border-left:4px solid #3b82f6;box-shadow:-4px 0 8px rgba(59,130,246,0.20), 0 0 0 3px rgba(220,38,38,0.25);}}
  .ai-color-pill{{display:inline-block;background:#dbeafe;color:#1e40af;font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;border:1px solid #93c5fd;margin-top:3px;}}
  .ai-brand-pill{{display:inline-block;background:#fef9c3;color:#854d0e;font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;border:1px solid #fde68a;margin-top:3px;}}

  .card-img-wrap{{position:relative;cursor:pointer;border-radius:8px;background:#fff;display:flex;align-items:center;justify-content:center;height:180px;overflow:hidden; border:1px solid #111;flex-shrink:0;}}
  .card-img-wrap::before{{content:'';position:absolute;inset:0;background:linear-gradient(90deg,#FFF8F2 25%,#FFEFE5 50%,#FFF8F2 75%);background-size:200% 100%;animation:shimmer 1.4s infinite;z-index:1;}}
  .card-img-wrap.img-loaded::before{{display:none;}}
  @keyframes shimmer{{0%{{background-position:200% 0}}100%{{background-position:-200% 0}}}}
  .card-img-placeholder{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:1;}}
  .card-img{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:2;opacity:0;transition:opacity .4s ease;}}
  .card-img.img-loaded{{opacity:1;}}
  .card.committed-rej .card-img{{filter:grayscale(80%);}}

  .warn-wrap{{position:absolute;top:8px;right:8px;display:flex;flex-direction:column;gap:6px;z-index:10;pointer-events:none;max-width:calc(100% - 16px);}}
  .warn-group{{display:flex;flex-direction:column;gap:4px;align-items:flex-end;}}
  .warn-group.inline{{flex-direction:row;flex-wrap:wrap;justify-content:flex-end;}}
  .warn-badge{{display:inline-flex;align-items:center;justify-content:center;max-width:100%;background:linear-gradient(90deg,#FFC107,#FF9800);color:#313133;font-size:9px;font-weight:800;padding:3px 8px;border-radius:9999px;box-shadow:0 2px 6px rgba(255,152,0,.3);animation:pulse 2s infinite;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .warn-badge.critical{{background:linear-gradient(90deg,#dc2626,#b91c1c);color:#fff;box-shadow:0 2px 6px rgba(220,38,38,.28);}}
  .warn-badge.advisory{{background:linear-gradient(90deg,#f59e0b,#f97316);color:#fff;box-shadow:0 2px 6px rgba(249,115,22,.22);animation:none;}}
  .warn-badge.info{{background:linear-gradient(90deg,#3b82f6,#2563eb);color:#fff;box-shadow:0 2px 6px rgba(37,99,235,.22);animation:none;}}
  .warn-badge.neutral{{background:linear-gradient(90deg,#6b7280,#4b5563);color:#fff;box-shadow:0 2px 6px rgba(75,85,99,.2);animation:none;}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.85}}}}
  .price-badge{{position:absolute;top:8px;left:8px;background:rgba(246,139,30,0.95);color:#fff;font-size:10px;font-weight:800;padding:3px 8px;border-radius:9999px;z-index:10;pointer-events:none;box-shadow:0 2px 6px rgba(0,0,0,.2);}}

  .meta{{font-size:11px;margin-top:8px;line-height:1.35;flex-grow:1;display:flex;flex-direction:column;gap:4px;}}
  .meta-core{{display:flex;flex-direction:column;gap:3px;}}
  .meta .nm{{font-weight:800;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:help;}}
  .meta .br{{color:{O};font-weight:800;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .meta .ct{{color:#666;font-size:10px;word-break:break-word;line-height:1.25;}}
  .meta .sl{{color:#999;font-size:9px;margin-top:2px;border-top:1px dashed #eee;padding-top:4px;cursor:help;display:flex;align-items:center;gap:6px;min-width:0;}}
  .meta .co{{color:#555;font-size:10px;margin-top:4px;background:#f0f0f0;padding:3px 5px;border-radius:4px;display:inline-block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;font-weight:600;}}
  .meta-extra{{border-top:1px solid #ececec;padding-top:4px;}}
  .meta-extra summary{{cursor:pointer;list-style:none;font-size:10px;font-weight:800;color:var(--accent);user-select:none;}}
  .meta-extra summary::-webkit-details-marker{{display:none;}}
  .meta-extra-body{{display:flex;flex-direction:column;gap:4px;padding-top:6px;}}
  .meta-extra .co{{margin-top:0;}}

  .acts{{display:flex;gap:4px;margin-top:auto;padding-top:8px;}}
  .act-btn{{flex:1;padding:6px;font-size:11px;border:none;border-radius:4px;cursor:pointer;font-weight:700;color:#fff;background:{O};}}
  .act-more{{flex:1;font-size:11px;border:1px solid #ccc;border-radius:4px;outline:none;cursor:pointer;background:#fff;}}

  .zoom-btn{{position:absolute;bottom:6px;right:6px;width:22px;height:22px;background:rgba(0,0,0,0.4);color:#fff;border-radius:4px;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:25;border:none;transition:background .2s;}}
  .zoom-btn:hover{{background:rgba(0,0,0,0.7);}}
  .zoom-btn svg{{width:12px;height:12px;flex-shrink:0;}}

  .zoom-nav-btn {{ position: absolute; top: 50%; transform: translateY(-50%); background: rgba(0,0,0,0.5); color: white; border: none; font-size: 24px; padding: 10px; cursor: pointer; border-radius: 4px; z-index: 100001; }}
  .zoom-nav-btn:hover {{ background: rgba(0,0,0,0.8); }}
  .zoom-nav-btn.prev {{ left: -40px; }}
  .zoom-nav-btn.next {{ right: -40px; }}

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

  /* 🧠 Highlights & Trust Badges */
  .hlt {{ background: #fee2e2; color: #b91c1c; font-weight: 800; border-radius: 2px; padding: 0 2px; }}
  .trust-badge {{
    position: absolute; top: 10px; left: 10px;
    background: #ef4444; color: #fff; font-size: 10px; font-weight: 800;
    padding: 4px 8px; border-radius: 6px; z-index: 100;
    box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4);
    cursor: pointer; transition: transform 0.2s;
  }}
  .trust-badge:hover {{ transform: scale(1.1); background: #dc2626; }}

  /* Per-card undo shimmer */
  .card.undo-processing {{
    pointer-events: none;
  }}
  .card.undo-processing::after {{
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 8px;
    background: rgba(255,255,255,0.55);
    backdrop-filter: blur(2px);
    z-index: 30;
    animation: undoPulse 0.5s ease-in-out infinite alternate;
  }}
  @keyframes undoPulse {{
    from {{ opacity: 0.4; }}
    to   {{ opacity: 0.85; }}
  }}

  #zoom-tooltip  .ctrl-bar {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: rgba(255, 255, 255, 0.75);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(246, 139, 30, 0.2);
    position: sticky;
    top: 0;
    z-index: 100;
  }}


  @keyframes shimmer {{
    0% {{ background-position: -1000px 0; }}
    100% {{ background-position: 1000px 0; }}
  }}
  .skeleton {{
    background: #f6f7f8;
    background-image: linear-gradient(to right, #f6f7f8 0%, #edeef1 20%, #f6f7f8 40%, #f6f7f8 100%);
    background-repeat: no-repeat;
    background-size: 2000px 100%;
    animation: shimmer 2s infinite linear;
  }}

  .card {{
    background: #fff;
    border-radius: 12px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    border: 1px solid #eee;
    position: relative;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }}
  .card:hover {{
    transform: translateY(-6px) scale(1.01);
    box-shadow: 0 12px 30px rgba(0,0,0,0.12);
    z-index: 10;
  }}
  #zoom-backdrop {{
    display: none;
    position: fixed;
    top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(0,0,0,0.6);
    z-index: 99999;
    cursor: pointer;
  }}
  #zoom-tooltip {{
    display: none;
    position: fixed;
    z-index: 100000;
    background: #fff;
    padding: 10px;
    border-radius: 8px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.4);
    border: 1px solid #ccc;
    width: 360px;
    height: 360px;
    transition: opacity 0.2s ease;
  }}
  #tooltip-img {{
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }}
  .tooltip-close {{
    position: absolute;
    top: -12px;
    right: -12px;
    background: #333;
    color: #fff;
    border-radius: 50%;
    width: 28px;
    height: 28px;
    border: 2px solid #fff;
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
  }}
  .tooltip-close:hover {{ background: #000; }}

  #custom-reason-panel {{
    display: none;
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    background: #fff;
    border: 2px solid {O};
    border-radius: 8px;
    padding: 16px 20px;
    z-index: 999999;
    box-shadow: 0 8px 32px rgba(0,0,0,0.25);
    min-width: 340px;
    max-width: 480px;
  }}
  #custom-reason-panel h4 {{ margin: 0 0 10px 0; font-size: 13px; color: #333; }}
  #custom-reason-input {{
    width: 100%;
    padding: 8px 10px;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: 13px;
    margin-bottom: 10px;
    box-sizing: border-box;
  }}
  #custom-reason-input:focus {{ outline: 2px solid {O}; border-color: {O}; }}
  .custom-panel-btns {{ display: flex; gap: 8px; }}
  .custom-panel-btns button {{ flex: 1; padding: 7px; border-radius: 4px; font-size: 12px; font-weight: 700; cursor: pointer; border: none; }}
  .custom-panel-confirm {{ background: {O}; color: #fff; }}
  .custom-panel-confirm:hover {{ opacity: 0.88; }}
  .custom-panel-cancel {{ background: #e0e0e0; color: #333; }}
  .custom-panel-cancel:hover {{ background: #ccc; }}

  input[type="search"]::-webkit-search-cancel-button {{
    -webkit-appearance: searchfield-cancel-button;
    cursor: pointer;
    width: 16px;
    height: 16px;
    margin-left: 8px;
  }}
</style>
</head>
<body>

<div id="custom-reason-panel">
  <h4>{labels_dict['custom_reason_title']}</h4>
  <input id="custom-reason-input" type="text" placeholder="{labels_dict['custom_reason_ph']}" maxlength="200">
  <div class="custom-panel-btns">
    <button class="custom-panel-confirm" onclick="confirmCustomReason()">{labels_dict['custom_apply']}</button>
    <button class="custom-panel-cancel" onclick="cancelCustomReason()">{labels_dict['custom_cancel']}</button>
  </div>
</div>

<div id="lang-loading" style="display:none;position:fixed;inset:0;background:rgba(255,255,255,0.8);z-index:9999999;align-items:center;justify-content:center;flex-direction:column;">
  <div style="width:40px;height:40px;border:4px solid #f3f3f3;border-top:4px solid {O};border-radius:50%;animation:spin 1s linear infinite;"></div>
  <style>@keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}</style>
  <div style="margin-top:16px;font-weight:700;color:#333;font-size:16px;">Updating Language...</div>
</div>

<div class="ctrl-bar top-bar">

  <button id="dark-toggle" onclick="toggleDark()">{labels_dict['dark_mode']}</button>
  <select id="iframe-lang-sel" class="reason-sel" style="max-width:60px;" onchange="document.getElementById('lang-loading').style.display='flex'; sendMsg('change_lang', this.value)" title="Change Language">
    <option value="en" {"selected" if lang=="en" else ""}>EN</option>
    <option value="fr" {"selected" if lang=="fr" else ""}>FR</option>
    <option value="ar" {"selected" if lang=="ar" else ""}>AR</option>
  </select>

  <span class="sel-count-text" style="font-weight:700; color:var(--accent); font-size:13px;">0 {labels_dict["items_pending"]}</span>
  <select class="reason-sel" id="batch-reason-top">
    <option value="REJECT_POOR_IMAGE">{labels_dict['poor_img']}</option>
    <option value="REJECT_IMG_STRETCHED">{labels_dict['img_stretched']}</option>
    <option value="REJECT_IMG_BLURRY">{labels_dict['img_blurry']}</option>
    <option value="REJECT_IMG_MISMATCH">{labels_dict['img_mismatch']}</option>
    <option value="REJECT_IMG_INFRINGING">{labels_dict['img_infringing']}</option>
    <option value="REJECT_IMG_TOO_MANY">{labels_dict['img_too_many']}</option>
    <option value="REJECT_WRONG_CAT">{labels_dict['wrong_cat']}</option>
    <option value="REJECT_FAKE">{labels_dict['fake_prod']}</option>
    <option value="REJECT_BRAND">{labels_dict['restr_brand']}</option>
    <option value="REJECT_WRONG_BRAND">{labels_dict.get('wrong_brand', 'Wrong Brand')}</option>
    <option value="REJECT_PROHIBITED">{labels_dict.get('prohibited', 'Prohibited')}</option>
    <option value="REJECT_COLOR">{labels_dict.get('missing_color', 'Missing Color')}</option>
    <option value="REJECT_FDA">FDA</option>
    <option value="REJECT_DUPLICATE">{labels_dict.get('sort_duplicates', 'Duplicate')}</option>
    <option value="OTHER_CUSTOM">{labels_dict.get('other_custom', 'Other (Custom)')}</option>
  </select>
  <button class="batch-btn" onclick="doBatchReject('top')">{labels_dict["batch_reject"]}</button>
  <button class="desel-btn" onclick="doBatchUndo()">{labels_dict["undo"]}</button>
  <button class="desel-btn" onclick="window.doSelectAll()">{labels_dict["select_all"]}</button>
  <button class="desel-btn" onclick="doDeselAll()">{labels_dict["deselect_all"]}</button>
  <button class="batch-btn top-btn" onclick="window.scrollTo(0, document.body.scrollHeight)">{_t("go_bottom")}</button>
  {sort_html}
  {filter_html}
</div>

<div id="shortcut-help" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
  z-index:9999999;align-items:center;justify-content:center;">
  <div style="background:var(--card);color:var(--text);border-radius:16px;padding:32px;min-width:280px;box-shadow:0 20px 50px rgba(0,0,0,0.3);">
    <h3 style="margin:0 0 16px">Keyboard Shortcuts</h3>
    <table style="border-collapse:collapse;width:100%;font-size:14px;">
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">j</kbd> / <kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">→</kbd></td><td style="padding-left:10px;">Next card</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">k</kbd> / <kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">←</kbd></td><td style="padding-left:10px;">Prev card</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">a</kbd></td><td style="padding-left:10px;">Approve focused</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">r</kbd></td><td style="padding-left:10px;">Reject focused</td></tr>
      <tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 0;"><kbd style="background:#eee;padding:2px 6px;border-radius:4px;color:#000;">?</kbd></td><td style="padding-left:10px;">Toggle help</td></tr>
    </table>
    <button onclick="document.getElementById('shortcut-help').style.display='none'"
      style="margin-top:20px;width:100%;padding:10px;border-radius:8px;
      background:var(--accent);color:#fff;border:none;cursor:pointer;font-weight:700;">Got it!</button>
  </div>
</div>

<div class="grid" id="card-grid"></div>

<div class="ctrl-bar bottom-bar">
  <span class="sel-count-text" style="font-weight:700; color:var(--accent); font-size:13px;">0 {labels_dict["items_pending"]}</span>
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
    <option value="REJECT_FDA">FDA</option>
    <option value="REJECT_DUPLICATE">{labels_dict["sort_duplicates"]}</option>
    <option value="OTHER_CUSTOM">Other Reason (Custom)</option>
  </select>
  <button class="batch-btn" onclick="doBatchReject('bottom')">{labels_dict["batch_reject"]}</button>
  <button class="desel-btn" onclick="doBatchUndo()">{labels_dict["undo"]}</button>
  <button class="desel-btn" onclick="window.doSelectAll()">{labels_dict["select_all"]}</button>
  <button class="desel-btn" onclick="doDeselAll()">{labels_dict["deselect_all"]}</button>
  <button class="desel-btn top-btn" onclick="window.scrollTo(0, 0)">{labels_dict["undo"]}</button>
</div>

<div id="zoom-backdrop" onclick="closeZoom()"></div>
<div id="zoom-tooltip">
  <button class="zoom-nav-btn prev" onclick="event.stopPropagation(); window.zoomMove(-1)" title="Previous">&#10094;</button>
  <img id="tooltip-img" alt="Zoomed product" referrerpolicy="no-referrer">
  <button class="zoom-nav-btn next" onclick="event.stopPropagation(); window.zoomMove(1)" title="Next">&#10095;</button>
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
        '.stAppViewBlock iframe[title="st.iframe"], .stAppViewBlock iframe[title="streamlit.components.v1.html"] {{',
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
          
          // Let Streamlit hide the iframe if it's being pooled or placed in a hidden container
          if (el.closest('[style*="display: none"]') || el.closest('[style*="display:none"]') || el.closest('[data-testid="stHidden"]')) return;
          
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
  }} catch(e) {{  }}
}})();

// blockOutsideClicks removed — dismissible=False in Python handles this
// and the capture listeners were preventing Streamlit buttons from firing

function escapeHtml(u){{return(u||"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");}}
var CARDS = {cards_json};
var COMMITTED = {committed_json};
var POOR_IMG_SIDS = new Set({poor_img_sids_json});
var PREFETCH_URLS = {prefetch_json};
var PLACEHOLDER = "{_PLACEHOLDER_SVG}";
var LABELS = {labels_json};
var DEFAULT_PAGE_SIZE = CARDS.length || 50;
var PAGE_SIZE_OPTIONS = [20, 50, 100, 200, 500];
var PAGE_STATE = {{
  pageSize: DEFAULT_PAGE_SIZE,
  page: 1,
  search: '',
  sellers: [],
  categories: []
}};

window._gridSelected = window._gridSelected || {{}};
window._stagedRejections = window._stagedRejections || {{}};
window.currentZoomSid = null;
window._imageIssues = window._imageIssues || {{}};
CARDS.forEach(c => {{
  if (c.warnings && c.warnings.length) {{
    if (!window._imageIssues[c.sid]) window._imageIssues[c.sid] = [];
    c.warnings.forEach(w => {{ if (!window._imageIssues[c.sid].includes(w)) window._imageIssues[c.sid].push(w); }});
  }}
}});
window._currentSort = window._currentSort || '';

window._pendingUndos = window._pendingUndos || {{}};
window._undoTimer = null;

var selected = window._gridSelected;
var staged = window._stagedRejections;

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

function sendMsg(type, payload) {{
  try {{
    var par = window.parent;
    var inputs = par.document.querySelectorAll('input[type="text"]');
    var bridge = null;
    var targetPlaceholder = (type === 'change_lang') ? 'LANG_BRIDGE_DO_NOT_USE' : 'JTBRIDGE_UNIQUE_DO_NOT_USE';
    for (var i = 0; i < inputs.length; i++) {{
      if (inputs[i].placeholder === targetPlaceholder || (targetPlaceholder === 'JTBRIDGE_UNIQUE_DO_NOT_USE' && inputs[i].getAttribute('aria-label') === 'jtbridge')) {{
        bridge = inputs[i]; break;
      }}
    }}
    if (!bridge) {{ console.warn('sendMsg: bridge not found for', type); return; }}
    var msg = JSON.stringify({{action: type, payload: payload}});
    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(par.HTMLInputElement.prototype, 'value').set;
    nativeInputValueSetter.call(bridge, msg);
    bridge.dispatchEvent(new par.Event('input', {{bubbles: true}}));
    bridge.focus({{preventScroll: true}});
    bridge.dispatchEvent(new par.KeyboardEvent('keydown', {{bubbles:true,cancelable:true,key:'Enter',keyCode:13}}));
    bridge.dispatchEvent(new par.KeyboardEvent('keyup',   {{bubbles:true,cancelable:true,key:'Enter',keyCode:13}}));
    bridge.blur();
  }} catch(ex) {{ console.error('jtbridge error:', ex); }}
}}

function scrollToTop() {{
  window.scrollTo({{top: 0, behavior: 'smooth'}});
}}

function updateParentPagination() {{
  // Parent pagination control removed — Streamlit handles Prev/Next natively
}}

function onImgLoad(img, sid) {{
  img.classList.remove('skeleton');
  img.classList.add('img-loaded');
  var wrap = img.closest('.card-img-wrap');
  if (wrap) wrap.classList.add('img-loaded');
  var w = img.naturalWidth, h = img.naturalHeight;
  var warns = [];
  if (w > 0 && h > 0) {{
    if (w < 250 || h < 250) warns.push('Low Resolution');
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
    'Duplicate product':      ['REJECT_DUPLICATE',      'Duplicate Product'],
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
    ['REJECT_FDA',           'FDA'],
    ['REJECT_DUPLICATE',     'Duplicate Product'],
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
  var cls = 'card'
    + (isCommitted ? ' committed-rej' + (isPoorImgRej ? ' poor-img-rej' : '') + (isBrandImgRej ? ' brand-image-rej' : '') : isStaged ? ' staged-rej' : '')
    + (card.is_manual_review && !isCommitted && !isStaged && !isSelected ? ' manual-review' : '')
    + (isSelected ? ' selected' : '')
    + (card.is_zip ? ' zip-card' : '');

  var safeImgSrcForHtml = card.img ? card.img.replace(/'/g, "%27").replace(/"/g, "%22") : PLACEHOLDER;
  var shortName = card.name.length > 38 ? escapeHtml(card.name.slice(0,38)) + '\u2026' : escapeHtml(card.name);
  var warnHtml = (card.warnings || []).map(w => `<span class="warn-badge">${{escapeHtml(w)}}</span>`).join('');
  if (card.is_duplicate) warnHtml += `<span class="warn-badge" style="background:#7c3aed;color:#fff;font-weight:800;">⧉ DUPLICATE</span>`;
  if (card.is_manual_review) {{
    var mrText = card.qc_skip_reason ? `👁 MANUAL REVIEW: ${{escapeHtml(card.qc_skip_reason)}}` : `👁 MANUAL REVIEW`;
    warnHtml += `<span class="warn-badge" style="background:#dc2626;color:#fff;font-weight:800;" title="${{escapeHtml(card.qc_skip_reason || 'Manual Review')}}">${{mrText}}</span>`;
  }}
  if (card.color_mismatch) warnHtml += `<span class="warn-badge" style="background:#b45309;color:#fff;" title="${{escapeHtml(card.color_mismatch)}}">⚠ Color Mismatch</span>`;
  var priceText = String(card.price || '').trim();
  var priceHtml = priceText ? `<div class="price-badge">${{escapeHtml(priceText)}}</div>` : '';

  var colorLabel = card.is_manual_review ? 'Color-M' : 'Color';
  var colorHtml = card.color ? `<div class="co" title="${{colorLabel}}: ${{escapeHtml(card.color)}}">${{colorLabel}}: ${{escapeHtml(card.color)}}</div>` : '';
  var aiColorHtml = (card.color_ai && card.color_ai !== card.color) ? `<div class="ai-color-pill" title="AI detected: ${{escapeHtml(card.color_ai)}}">🎨 AI Color: ${{escapeHtml(card.color_ai)}}</div>` : '';
  var colorMismatchHtml = card.color_mismatch ? `<div class="co" style="color:#b45309;border-color:#fde68a;" title="${{escapeHtml(card.color_mismatch)}}">⚠ ${{escapeHtml(card.color_mismatch)}}</div>` : '';
  var catReasonHtml = (card.cat_reason && (card.warnings||[]).some(w => w.includes('Category'))) ?
    `<div class="co" style="color:#9333ea;font-size:10px;white-space:normal;line-height:1.3;" title="${{escapeHtml(card.cat_reason)}}">${{escapeHtml(card.cat_reason.length > 80 ? card.cat_reason.slice(0,80)+'…' : card.cat_reason)}}</div>` : '';
  var suggestedCatHtml = card.suggested_cat ? `<div class="co" style="color:#0369a1;" title="AI suggests: ${{escapeHtml(card.suggested_cat)}}">→ ${{escapeHtml(card.suggested_cat.length > 50 ? card.suggested_cat.slice(0,50)+'…' : card.suggested_cat)}}</div>` : '';
  var aiBrandHtml = (card.brand_detected && card.brand_detected.toLowerCase() !== card.brand.toLowerCase()) ? `<div class="ai-brand-pill" title="AI detected brand: ${{escapeHtml(card.brand_detected)}}">🏷 AI Brand: ${{escapeHtml(card.brand_detected)}}</div>` : '';
  var brandDetectedHtml = (isBrandImgRej && card.brand_detected) ? '<div class="co" style="background:#E8F5E9;color:#2E7D32;border:1px solid #C8E6C9;" title="Brand Detected: ' + escapeHtml(card.brand_detected) + '">Detected Brand: ' + escapeHtml(card.brand_detected) + '</div>' : '';
  var zipBadgeHtml = card.is_zip ? '<span style="background:linear-gradient(135deg,#3b82f6,#1d4ed8);color:#fff;font-size:10px;font-weight:900;padding:2px 8px;border-radius:6px;box-shadow:0 2px 4px rgba(0,0,0,0.15);margin-left:8px;display:inline-block;">ZIP</span>' : '';
  if (card.zip_override) {{
    var overrideType = card.zip_override === 'color' ? 'Color' : (card.zip_override === 'volume' ? 'Weight/Volume' : 'Warranty');
    zipBadgeHtml += '<span style="background:linear-gradient(135deg,#10b981,#059669);color:#fff;font-size:10px;font-weight:900;padding:2px 8px;border-radius:6px;box-shadow:0 2px 4px rgba(0,0,0,0.15);margin-left:4px;display:inline-block;" title="Auto-approved by main ' + overrideType.toLowerCase() + ' check">🔓 ' + overrideType + ' Overridden</span>';
  }}

  var zoomHtml = `<button class="zoom-btn" onclick="event.stopPropagation();showZoom('${{safeSid}}', event)" title="Preview">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      <line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>
    </svg></button>`;

  var imgIdx = CARDS.indexOf(card);
  var isEager = imgIdx < {cols_per_row * 2};
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
      <div class="br" title="${{escapeHtml(card.brand)}}">Brand${{card.is_manual_review ? '-M' : ''}}: ${{escapeHtml(card.brand)}}</div>
      ${{aiBrandHtml}}
      <div class="ct" title="${{escapeHtml(card.cat)}}">Category${{card.is_manual_review ? '-M' : ''}}: ${{escapeHtml(card.cat)}}</div>
      <div class="sl" title="${{escapeHtml(card.seller)}}" style="display:flex;align-items:center;">
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Seller: ${{escapeHtml(card.seller)}}</span>
        ${{zipBadgeHtml}}
      </div>
      ${{colorHtml}}
      ${{aiColorHtml}}
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
  document.getElementById('zoom-backdrop').style.display = 'block';
  tooltip.style.display = 'block';
  window.currentZoomSid = sid;
  var tw = 360, th = 360;
  var x = event.clientX, y = event.clientY;
  var vw = window.innerWidth, vh = window.innerHeight;
  var left = x + 15;
  if (left + tw > vw - 10) left = x - tw - 15;
  if (left < 10) left = 10;
  var top = y - (th / 2);
  if (top < 10) top = 10;
  if (top + th > vh - 10) top = vh - th - 10;
  tooltip.style.position = 'fixed';
  tooltip.style.left = left + 'px';
  tooltip.style.top = top + 'px';
}};

window.closeZoom = function() {{
  document.getElementById('zoom-tooltip').style.display = 'none';
  document.getElementById('zoom-backdrop').style.display = 'none';
  window.currentZoomSid = null;
}};

window.zoomMove = function(dir) {{
  if (!window.currentZoomSid) return;
  var displayCards = getDisplayCards();
  var idx = displayCards.findIndex(c => c.sid === window.currentZoomSid);
  if (idx < 0) return;
  var nextIdx = idx + dir;
  if (nextIdx < 0) nextIdx = displayCards.length - 1;
  if (nextIdx >= displayCards.length) nextIdx = 0;
  var nextSid = displayCards[nextIdx].sid;

  var el = document.getElementById('card-' + escapeHtml(nextSid));
  if (el) el.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});

  var fakeEvent = {{ clientX: window.innerWidth/2, clientY: window.innerHeight/2 }};
  window.currentZoomSid = null;
  window.showZoom(nextSid, fakeEvent);
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
  updateParentPagination();
}}

window._currentFilter = window._currentFilter || '';

function _shortText(text, maxLen) {{
  var t = String(text || '').trim();
  if (!t) return '';
  return t.length > maxLen ? t.slice(0, maxLen) + '\u2026' : t;
}}

function _readMultiSelectValues(id) {{
  var el = document.getElementById(id);
  if (!el) return [];
  return Array.from(el.selectedOptions || []).map(function(opt) {{ return opt.value; }}).filter(Boolean);
}}

function _setMultiSelectValues(id, values) {{
  var el = document.getElementById(id);
  if (!el) return;
  var set = new Set(values || []);
  Array.from(el.options).forEach(function(opt) {{ opt.selected = set.has(opt.value); }});
}}

function _pageSizeValue() {{
  return Math.max(1, parseInt(PAGE_STATE.pageSize || DEFAULT_PAGE_SIZE, 10) || DEFAULT_PAGE_SIZE);
}}

function getSortedCards() {{
  var sort = window._currentSort;
  if (!sort) return CARDS;
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

function getBaseFilteredCards() {{
  var cards = getSortedCards();
  var f = window._currentFilter;
  if (f) {{
    if (f === 'committed') cards = cards.filter(function(c) {{ return c.sid in COMMITTED; }});
    else if (f === 'brand_ocr') cards = cards.filter(function(c) {{ return c.sid in COMMITTED && (COMMITTED[c.sid]||'').includes('Brand Image Check'); }});
    else if (f === 'no_flags') cards = cards.filter(function(c) {{ return !(c.warnings||[]).length && !(c.sid in COMMITTED) && !(c.sid in staged); }});
    else if (f === 'duplicates') cards = cards.filter(function(c) {{ return c.is_duplicate; }});
    else if (f === 'manual_review') cards = cards.filter(function(c) {{ return c.is_manual_review; }});
    else if (f === 'color_mismatch') cards = cards.filter(function(c) {{ return !!c.color_mismatch; }});
    else cards = cards.filter(function(c) {{
      var inWarnings = (c.warnings||[]).some(function(w) {{ return w === f; }});
      var inCommitted = c.sid in COMMITTED && (COMMITTED[c.sid]||'').replace(/_/g,' ').toLowerCase() === f.replace(/_/g,' ').toLowerCase();
      return inWarnings || inCommitted;
    }});
  }}
  var q = (PAGE_STATE.search || '').toLowerCase().trim();
  if (q) {{
    cards = cards.filter(function(c) {{
      var text = [c.name, c.brand, c.sid, c.cat, c.seller].join(' ').toLowerCase();
      return text.includes(q);
    }});
  }}
  if (PAGE_STATE.sellers && PAGE_STATE.sellers.length) {{
    var sellerSet = new Set(PAGE_STATE.sellers);
    cards = cards.filter(function(c) {{ return sellerSet.has(c.seller); }});
  }}
  if (PAGE_STATE.categories && PAGE_STATE.categories.length) {{
    var catSet = new Set(PAGE_STATE.categories);
    cards = cards.filter(function(c) {{ return catSet.has(c.cat); }});
  }}
  return cards;
}}

function getDisplayCards() {{
  var cards = getBaseFilteredCards();
  var pageSize = _pageSizeValue();
  var totalPages = Math.max(1, Math.ceil(cards.length / pageSize));
  if (PAGE_STATE.page > totalPages) PAGE_STATE.page = totalPages;
  if (PAGE_STATE.page < 1) PAGE_STATE.page = 1;
  var start = (PAGE_STATE.page - 1) * pageSize;
  return cards.slice(start, start + pageSize);
}}

function _updateFilterSummary(filteredCount) {{
  var sellerCount = (PAGE_STATE.sellers || []).length;
  var catCount = (PAGE_STATE.categories || []).length;
  var searchActive = !!(PAGE_STATE.search || '').trim();
  var searchCount = document.getElementById('search-count');
  if (searchCount) searchCount.textContent = searchActive ? 'Active' : '0';
  var sellerCountEl = document.getElementById('seller-selected-count');
  if (sellerCountEl) sellerCountEl.textContent = 'Selected: ' + sellerCount;
  var catCountEl = document.getElementById('category-selected-count');
  if (catCountEl) catCountEl.textContent = 'Selected: ' + catCount;
  var pageCountEl = document.getElementById('page-count');
  if (pageCountEl) pageCountEl.textContent = 'Page ' + PAGE_STATE.page;
  var summaryEl = document.getElementById('filter-summary');
  if (summaryEl) {{
    var parts = [];
    if (searchActive) parts.push('Name: ' + _shortText(PAGE_STATE.search, 24));
    parts.push('Seller: ' + sellerCount + ' selected');
    parts.push('Category: ' + catCount + ' selected');
    summaryEl.textContent = parts.join(' | ');
  }}
}}

function _updatePageControls(totalCount) {{
  var pageSize = _pageSizeValue();
  var totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  if (PAGE_STATE.page > totalPages) PAGE_STATE.page = totalPages;
  if (PAGE_STATE.page < 1) PAGE_STATE.page = 1;
  var pageInfo = document.getElementById('page-info');
  if (pageInfo) pageInfo.textContent = 'Page ' + PAGE_STATE.page + ' / ' + totalPages;
  var prev = document.getElementById('page-prev');
  var next = document.getElementById('page-next');
  if (prev) prev.disabled = PAGE_STATE.page <= 1;
  if (next) next.disabled = PAGE_STATE.page >= totalPages;
}}

window.goPage = function(delta) {{
  var total = getBaseFilteredCards().length;
  var totalPages = Math.max(1, Math.ceil(total / _pageSizeValue()));
  PAGE_STATE.page = Math.max(1, Math.min(totalPages, PAGE_STATE.page + delta));
  renderAll();
}};

window.resetAllFilters = function() {{
  PAGE_STATE.search = '';
  PAGE_STATE.sellers = [];
  PAGE_STATE.categories = [];
  PAGE_STATE.page = 1;
  _setMultiSelectValues('seller-filter', []);
  _setMultiSelectValues('category-filter', []);
  var searchEl = document.getElementById('grid-search');
  if (searchEl) searchEl.value = '';
  renderAll();
}};

window._syncFilterState = function() {{
  PAGE_STATE.search = (document.getElementById('grid-search') || {{ value: '' }}).value || '';
  PAGE_STATE.sellers = _readMultiSelectValues('seller-filter');
  PAGE_STATE.categories = _readMultiSelectValues('category-filter');
  var pageSizeSel = document.getElementById('page-size-sel');
  if (pageSizeSel) PAGE_STATE.pageSize = parseInt(pageSizeSel.value, 10) || DEFAULT_PAGE_SIZE;
  PAGE_STATE.page = 1;
  renderAll();
}};

window.applySort = function(val) {{
  window._currentSort = val;
  ['sort-sel-top','sort-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value=val; }});
  renderAll();
}};

window.applyFilter = function(val) {{
  window._currentFilter = val;
  ['filter-sel-top','filter-sel-bottom'].forEach(function(id) {{ var el=document.getElementById(id); if(el) el.value=val; }});
  renderAll();
}};

function renderAll() {{
  var cards = getDisplayCards();
  document.getElementById('card-grid').innerHTML = cards.map(renderCard).join('');
  var countEl = document.getElementById('grid-count');
  if (countEl) countEl.textContent = cards.length + ' products' + (window._currentFilter ? ' (filtered)' : '');
  updateSelCount(); activateLazyImages();
}}

function replaceCard(sid) {{
  var el = document.getElementById('card-' + escapeHtml(sid));
  if (!el) return;
  var card = CARDS.find(c => c.sid === sid);
  if (card) {{ var t = document.createElement('div'); t.innerHTML = renderCard(card); el.replaceWith(t.firstElementChild); activateLazyImages(); }}
}}

window.doSelectAll = function() {{
  CARDS.forEach(c => {{ if (!(c.sid in staged)) selected[c.sid] = true; }});
  renderAll();
  updateSelCount();
}};

window.toggleSelect = function(sid, e) {{
  var t = e && e.target;
  if (t && (t.tagName === 'SELECT' || t.tagName === 'OPTION' || t.tagName === 'BUTTON' || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.closest('select,button,input,textarea,a'))) return;
  if (sid in staged) delete staged[sid];
  else if (sid in selected) delete selected[sid];
  else selected[sid] = true;
  replaceCard(sid); updateSelCount();
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
  var selectId = pos === 'top' ? 'batch-reason-top' : 'batch-reason-bottom';
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
  if (Object.keys(commentPayload).length) sendMsg('reject_comments', commentPayload);
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
  sendMsg('reject', payload);
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
  var i = 0, done = 0, total = PREFETCH_URLS.length;
  function loadNext() {{
    if (i >= total) return;
    var url = PREFETCH_URLS[i++];
    var img = new Image();
    img.referrerPolicy = "no-referrer";
    img.onload = img.onerror = function() {{
      done++;
      if (statusEl) statusEl.textContent = 'Prefetched ' + done + '/' + total;
      loadNext();
    }};
    img.src = url;
  }}
  for (var j = 0; j < 8; j++) {{
    loadNext();
  }}
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
  document.querySelectorAll('.card').forEach(function(c) {{ c.style.borderLeft = ''; }});
  var el = document.getElementById('card-' + escapeHtml(_focusedSid));
  if (el) {{
    el.style.borderLeft = '4px solid #2196F3';
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

['batch-reason-top','batch-reason-bottom'].forEach(function(id) {{
  var el = document.getElementById(id);
  if (el) el.addEventListener('change', function() {{ _lastReason = this.value; }});
}});

(function() {{
  var _gs = document.getElementById('grid-search');
  if (_gs) _gs.addEventListener('input', function() {{
    var q = this.value.toLowerCase().trim();
    document.querySelectorAll('.card').forEach(function(card) {{
      var text = (card.dataset.name + ' ' + card.dataset.brand + ' ' +
                  card.dataset.sid + ' ' + card.dataset.cat).toLowerCase();
      card.style.display = (!q || text.includes(q)) ? '' : 'none';
    }});
    var vis = document.querySelectorAll('.card:not([style*="none"])').length;
    var el = document.getElementById('grid-count');
    if (el) el.textContent = q ? vis + ' matching' : vis + ' products';
  }});
}})();

var _dark = false;
try {{ if (typeof localStorage !== 'undefined') {{ _dark = localStorage.getItem('gridDark') === '1'; }} }} catch(e) {{}}
window.applyDark = function(on) {{
  document.documentElement.style.setProperty('--bg',    on ? '#18181b' : '#f9fafb');
  document.documentElement.style.setProperty('--card',  on ? '#27272a' : '#ffffff');
  document.documentElement.style.setProperty('--text',  on ? '#f4f4f5' : '#111827');
  document.documentElement.style.setProperty('--border',on ? '#3f3f46' : '#e5e7eb');
  var dtEl = document.getElementById('dark-toggle');
  if (dtEl) dtEl.textContent = on ? 'Light' : 'Dark';
  try {{ localStorage.setItem('gridDark', on ? '1' : '0'); }} catch(e) {{}}
}}
window.toggleDark = function() {{ _dark = !_dark; applyDark(_dark); }}
try {{ applyDark(_dark); }} catch(e) {{}}

window.batchApproveSingle = function(sid) {{
  window.parent.postMessage({{type:'staged_reject', sid:sid, reason:'Approved'}}, '*');
}}

window.batchApprove = function() {{
  var sids = Object.keys(selected).filter(s => !(s in COMMITTED));
  if (sids.length === 0) return;
  if (confirm('Approve ' + sids.length + ' selected items?')) {{
    sids.forEach(sid => window.batchApproveSingle(sid));
    window.doDeselAll();
  }}
}}

try {{
  renderAll();
}} catch(e) {{
  document.getElementById('card-grid').innerHTML = '<div style="color:red;padding:20px;font-size:14px;font-family:monospace;white-space:pre-wrap;background:#fff3f3;border:2px solid red;border-radius:8px;margin:20px;">&#x26A0; JS ERROR in renderAll():<br>' + String(e) + '<br><br>Stack:<br>' + (e.stack||'') + '</div>';
}}

</script>

<div id="cols-strip" style="position:fixed;bottom:0;left:0;width:100%;z-index:9999;background:var(--card);border-top:1px solid var(--border);display:flex;align-items:center;gap:6px;padding:3px 12px;height:26px;opacity:0.55;transition:opacity .2s;" onmouseenter="this.style.opacity='1'" onmouseleave="this.style.opacity='0.55'">
  <span style="font-size:10px;font-weight:700;color:var(--text);opacity:.6;white-space:nowrap;letter-spacing:.5px;">⊞ COLS</span>
  {_cols_btns}
</div>

</body>
</html>"""


@st.dialog(
    "Visual Review Mode", width="large", icon=":material/pageview:", dismissible=False
)
def visual_review_modal(support_files):
    scroll_top_flag = st.session_state.get("do_scroll_top", False)
    st.session_state.do_scroll_top = False
    _wide_cols = st.session_state.get("grid_cols_per_row", 5) >= 6
    if _wide_cols:
        st.markdown("""
        <style>
        [data-testid="stDialog"] > div > div[role="dialog"] {
            max-width: 96vw !important;
            width: 96vw !important;
        }
        </style>""", unsafe_allow_html=True)
    def _clear_grid_search_n():
        st.session_state["grid_search_n"] = ""
    def _clear_grid_filter_sellers():
        st.session_state["grid_filter_sellers"] = []
    def _clear_grid_filter_categories():
        st.session_state["grid_filter_categories"] = []
    def _clear_grid_filter_flag():
        st.session_state["grid_filter_flag"] = ""
    def _clear_grid_sort_issue():
        st.session_state["grid_sort_issue"] = ""

    fr = st.session_state.final_report
    data = st.session_state.all_data_map
    all_rows = st.session_state.get("all_data_rows", data)
    committed_rej_sids = {
        k.replace("quick_rej_", "")
        for k in st.session_state.keys()
        if k.startswith("quick_rej_") and "reason" not in k
    }

    poor_img_rej_sids = set(
        fr[
            (fr["Status"] == "Rejected")
            & (
                fr["FLAG"].isin(["Poor images", "Image Stretched", "Image Blurry"])
                | fr["FLAG"].str.contains("Brand Image Check", na=False, case=False)
                | fr["Comment"].str.contains("Brand Image Check", na=False, case=False)
            )
        ]["ProductSetSid"]
        .astype(str)
        .str.strip()
        .unique()
    )
    valid_grid_df = fr[
        (fr["Status"] == "Approved")
        | (fr["ProductSetSid"].isin(committed_rej_sids))
        | (fr["ProductSetSid"].isin(poor_img_rej_sids))
    ]

    if "MAIN_IMAGE" not in data.columns:
        data["MAIN_IMAGE"] = ""

    _cached_review = st.session_state.get("_grid_review_data_cache")
    _cache_valid = (
        _cached_review is not None
        and not committed_rej_sids
        and not poor_img_rej_sids
        and len(_cached_review) > 0
    )
    if _cache_valid:
        review_data = _cached_review.copy()
    else:
        available_cols = [c for c in GRID_COLS if c in data.columns]
        if "CATEGORY_CODE" in data.columns and "CATEGORY_CODE" not in available_cols:
            available_cols.append("CATEGORY_CODE")
        if "IMAGE1_ZIP" in data.columns:
            available_cols.append("IMAGE1_ZIP")
        if "Brand_Detected_On_Product" in data.columns:
            available_cols.append("Brand_Detected_On_Product")
        review_data = pd.merge(
            valid_grid_df[["ProductSetSid"]],
            data[available_cols],
            left_on="ProductSetSid",
            right_on="PRODUCT_SET_SID",
            how="left",
        )
        _code_to_path = support_files.get("code_to_path", {})
        if _code_to_path and "CATEGORY_CODE" in review_data.columns:
            review_data = review_data.copy()
            review_data["CATEGORY"] = review_data["CATEGORY_CODE"].apply(
                lambda c: (
                    _code_to_path.get(str(c).strip(), str(c)) if pd.notna(c) else ""
                )
            )

    curr_search_n = st.session_state.get("grid_search_n", "")
    curr_sellers = st.session_state.get("grid_filter_sellers", [])
    curr_categories = st.session_state.get("grid_filter_categories", [])

    seller_base_df = review_data
    if curr_categories and "CATEGORY" in seller_base_df.columns:
        seller_base_df = seller_base_df[seller_base_df["CATEGORY"].astype(str).isin(curr_categories)]
    
    seller_opts = sorted(
        {
            str(v).strip()
            for v in seller_base_df.get("SELLER_NAME", pd.Series(dtype=str)).dropna().astype(str)
            if str(v).strip() and str(v).strip().lower() != "nan"
        }
    )

    category_base_df = review_data
    if curr_sellers and "SELLER_NAME" in category_base_df.columns:
        category_base_df = category_base_df[category_base_df["SELLER_NAME"].astype(str).isin(curr_sellers)]
    
    category_opts = sorted(
        {
            str(v).strip()
            for v in category_base_df.get("CATEGORY", pd.Series(dtype=str)).dropna().astype(str)
            if str(v).strip() and str(v).strip().lower() != "nan"
        }
    )

    for s in curr_sellers:
        if s not in seller_opts: seller_opts.append(s)
    for c in curr_categories:
        if c not in category_opts: category_opts.append(c)
    seller_opts.sort()
    category_opts.sort()

    c1, c2, c3, c4 = st.columns(
        [1.5, 1.5, 1.5, 0.8], gap="large", vertical_alignment="bottom"
    )
    with c1:
        c1a, c1b = st.columns([6, 1], vertical_alignment="bottom", gap="small")
        with c1a:
            search_n = st.text_input(
                "Search by Name, Brand, or SID", placeholder="Product name, brand, SID…", icon=":material/search:",
                key="grid_search_n",
            )
        with c1b:
            st.button("✖", key="clr_n", help="Clear search", on_click=_clear_grid_search_n, disabled=not bool(curr_search_n))
    with c2:
        c2a, c2b = st.columns([6, 1], vertical_alignment="bottom", gap="small")
        with c2a:
            search_sellers = st.multiselect(
                "Filter by Seller",
                options=seller_opts,
                default=curr_sellers,
                key="grid_filter_sellers",
            )
        with c2b:
            st.button("✖", key="clr_sellers", help="Clear seller filter", on_click=_clear_grid_filter_sellers, disabled=not bool(curr_sellers))
    with c3:
        c3a, c3b = st.columns([6, 1], vertical_alignment="bottom", gap="small")
        with c3a:
            search_categories = st.multiselect(
                "Filter by Category",
                options=category_opts,
                default=curr_categories,
                key="grid_filter_categories",
            )
        with c3b:
            st.button("✖", key="clr_categories", help="Clear category filter", on_click=_clear_grid_filter_categories, disabled=not bool(curr_categories))

    curr_flag = st.session_state.get("grid_filter_flag", "")
    curr_sort = st.session_state.get("grid_sort_issue", "")

    with c4:
        if st.button("✕ Close", key="close_modal_top", type="secondary", use_container_width=True):
            st.session_state.show_review_modal = False
            st.rerun()
        _wide = st.session_state.get("grid_cols_per_row", 5) >= 6
        _ipp_opts = [20, 50, 100, 200, 500] if _wide else [20, 50, 100, 200]

        _current_ipp = st.session_state.get("grid_items_per_page", 50)
        
        # Clamp it if options changed (e.g. wide mode turned off and we were at 500)
        if _current_ipp not in _ipp_opts:
            _current_ipp = 200 if 200 in _ipp_opts else _ipp_opts[-1]
            st.session_state.grid_items_per_page = _current_ipp
            
        _slider_key = f"grid_ipp_{_wide}"
        
        # Seed the widget's state key explicitly for this new key
        if _slider_key not in st.session_state:
            st.session_state[_slider_key] = _current_ipp

        def _on_ipp_change():
            st.session_state.grid_items_per_page = st.session_state[_slider_key]

        st.select_slider(
            "Items per page",
            options=_ipp_opts,
            key=_slider_key,
            on_change=_on_ipp_change,
        )
        st.session_state.grid_items_per_page = st.session_state[_slider_key]

    if "_grid_page_contexts" not in st.session_state:
        st.session_state._grid_page_contexts = {}
    _curr_ctx = (
        search_n or "",
        tuple(sorted(search_sellers)) if search_sellers else (),
        tuple(sorted(search_categories)) if search_categories else (),
        curr_flag,
        curr_sort,
    )
    _prev_ctx = st.session_state.get("_grid_last_ctx", ("", "", "", "", ""))
    if _curr_ctx != _prev_ctx:
        st.session_state._grid_page_contexts[_prev_ctx] = st.session_state.get("grid_page", 0)
        st.session_state.grid_page = st.session_state._grid_page_contexts.get(_curr_ctx, 0)
        st.session_state["_grid_last_ctx"] = _curr_ctx

    if search_n:
        n_mask = review_data["NAME"].astype(str).str.contains(search_n, case=False, na=False)
        b_mask = review_data.get("BRAND", pd.Series(dtype=str, index=review_data.index)).astype(str).str.contains(search_n, case=False, na=False)
        s_mask = review_data.get("ProductSetSid", pd.Series(dtype=str, index=review_data.index)).astype(str).str.contains(search_n, case=False, na=False)
        review_data = review_data[n_mask | b_mask | s_mask]
    if search_sellers:
        review_data = review_data[
            review_data["SELLER_NAME"].astype(str).isin(search_sellers)
        ]
    if search_categories and "CATEGORY" in review_data.columns:
        review_data = review_data[
            review_data["CATEGORY"].astype(str).isin(search_categories)
        ]

    if curr_flag or curr_sort:
        _fr_flag_map = fr.set_index("ProductSetSid")["FLAG"].to_dict() if "FLAG" in fr.columns else {}
        _fr_comment_map = fr.set_index("ProductSetSid")["Comment"].to_dict() if "Comment" in fr.columns else {}
        _zip_index = st.session_state.get("_zip_sid_index")
        _zip_status_cols = st.session_state.get("_zip_status_cols", [])
        _zip_prefetch_map = st.session_state.get("_zip_prefetch_map", {})
        _staged_sids = set(st.session_state.get("_stagedRejections", {}).keys())
        
        def get_warnings(sid):
            w = []
            comment = str(_fr_comment_map.get(sid, "")).lower()
            if "stretched" in comment or "tall" in comment: w.append("Tall (Screenshot?)")
            if "stretched" in comment or "wide" in comment: w.append("Wide Aspect")
            if "blurry" in comment or "low res" in comment or "resolution" in comment or "small" in comment: w.append("Low Resolution")
            
            flag = _fr_flag_map.get(sid)
            if pd.notna(flag) and flag not in ("Approved", "Manual review", "Approved by User"):
                w.append(flag)
                
            if _zip_index is not None and sid in _zip_index.index:
                zrow = _zip_index.loc[sid]
                if hasattr(zrow, "iloc") and hasattr(zrow, "shape") and len(zrow.shape) == 2:
                    zrow = zrow.iloc[0]
                for zcol in _zip_status_cols:
                    if str(zrow.get(zcol, "")).lower() == "rejected":
                        zflag = _zip_prefetch_map.get(zcol, zcol.replace("_Status", "").replace("_", " ").title())
                        if zflag not in w: w.append(zflag)
            return list(dict.fromkeys(w))

        review_data["_warnings"] = review_data["ProductSetSid"].apply(get_warnings)

    if curr_flag:
        if curr_flag == "committed":
            review_data = review_data[review_data["ProductSetSid"].isin(committed_rej_sids)]
        elif curr_flag == "brand_ocr":
            review_data = review_data[review_data["ProductSetSid"].isin(committed_rej_sids) & review_data["_warnings"].apply(lambda w: "Brand Image Check" in w)]
        elif curr_flag == "no_flags":
            review_data = review_data[(~review_data["ProductSetSid"].isin(committed_rej_sids)) & (~review_data["ProductSetSid"].isin(_staged_sids)) & review_data["_warnings"].apply(lambda w: len(w) == 0)]
        elif curr_flag == "duplicates":
            review_data = review_data[review_data.get("is_duplicate", pd.Series(False, index=review_data.index)).astype(bool)]
        elif curr_flag == "manual_review":
            review_data = review_data[review_data.get("is_manual_review", pd.Series(False, index=review_data.index)).astype(bool)]
        elif curr_flag == "color_mismatch":
            review_data = review_data[review_data.get("color_mismatch", pd.Series(False, index=review_data.index)).astype(bool)]
        else:
            def has_flag(sid, w):
                return curr_flag in w or (sid in committed_rej_sids and str(st.session_state.get(f"quick_rej_reason_{sid}", "")).replace("_", " ").lower() == curr_flag.replace("_", " ").lower())
            review_data = review_data[review_data.apply(lambda r: has_flag(r["ProductSetSid"], r["_warnings"]), axis=1)]

    if curr_sort:
        if curr_sort == "most_flagged":
            review_data["_w_count"] = review_data["_warnings"].apply(len)
            review_data = review_data.sort_values(by=["_w_count"], ascending=False).drop(columns=["_w_count"])
        elif curr_sort == "no_issue":
            review_data["_has_issue"] = review_data["_warnings"].apply(lambda w: 1 if len(w) > 0 else 0)
            review_data = review_data.sort_values(by=["_has_issue"], ascending=True).drop(columns=["_has_issue"])
        else:
            issue_map = {'low_res':'Low Resolution','tall':'Tall (Screenshot?)','wide':'Wide Aspect','broken':'Broken Image'}
            target = issue_map.get(curr_sort, curr_sort)
            review_data["_has_target"] = review_data["_warnings"].apply(lambda w: 0 if target in w else 1)
            review_data = review_data.sort_values(by=["_has_target"], ascending=True).drop(columns=["_has_target"])
    else:
        review_data = review_data.sort_values(
            by=["SELLER_NAME", "NAME"], na_position="last"
        ).reset_index(drop=True)

    ipp = st.session_state.get("grid_items_per_page", 50)
    total_pages = max(1, (len(review_data) + ipp - 1) // ipp)
    if st.session_state.get("grid_page", 0) >= total_pages:
        st.session_state.grid_page = 0

    st.markdown(f"<div style='margin-bottom:-10px;color:#6b7280;font-size:12px;'>Total items: {len(review_data)}</div>", unsafe_allow_html=True)

    # ------------------------------------------------------------------
    # Pagination controls
    #
    # IMPORTANT: `jump_top` and `jump_bot` are widget-backed session_state
    # keys (they belong to the two st.number_input widgets below). Once a
    # widget with a given key has been instantiated in a run, Streamlit
    # forbids writing to that key again in the *same* run (it raises
    # StreamlitAPIException). The old code tried to do exactly that from
    # the "Prev"/"Next" button branches (`if st.button(...): st.session_state.jump_top = ...`)
    # which broke as soon as both widgets existed on the page.
    #
    # The fix: drive all page changes through on_click / on_change
    # callbacks. Callbacks run *before* the script body (and therefore
    # before any widget is instantiated for that run), so it's always
    # safe to set jump_top / jump_bot / grid_page there. Streamlit
    # automatically reruns the script after a callback fires, so no
    # manual st.rerun() is needed either.
    # ------------------------------------------------------------------

    def _goto_page(new_page_0idx):
        new_page_0idx = max(0, min(total_pages - 1, new_page_0idx))
        st.session_state.grid_page = new_page_0idx
        st.session_state.jump_top = new_page_0idx + 1
        st.session_state.jump_bot = new_page_0idx + 1
        st.session_state.do_scroll_top = True

    def _prev_page():
        _goto_page(st.session_state.get("grid_page", 0) - 1)

    def _next_page():
        _goto_page(st.session_state.get("grid_page", 0) + 1)

    def _jump_from_widget(source_key):
        # Called on_change of whichever number_input the user actually typed into.
        new_page_0idx = st.session_state[source_key] - 1
        _goto_page(new_page_0idx)

    # Keep jump_top/jump_bot initialized (only before their widgets exist,
    # i.e. only if this is the very first time we see these keys).
    if "jump_top" not in st.session_state:
        st.session_state.jump_top = st.session_state.get("grid_page", 0) + 1
    if "jump_bot" not in st.session_state:
        st.session_state.jump_bot = st.session_state.get("grid_page", 0) + 1

    pg_cols = st.columns([1, 2, 1], vertical_alignment="bottom", gap="small")
    with pg_cols[0]:
        st.button(
            "⬅ Prev",
            key="prev_top",
            use_container_width=True,
            disabled=st.session_state.get("grid_page", 0) == 0,
            on_click=_prev_page,
        )
    with pg_cols[1]:
        st.number_input(
            f"Page (of {total_pages})",
            min_value=1,
            max_value=max(1, total_pages),
            key="jump_top",
            on_change=_jump_from_widget,
            args=("jump_top",),
        )
    with pg_cols[2]:
        st.button(
            "Next ➡",
            key="next_top",
            use_container_width=True,
            disabled=st.session_state.grid_page >= total_pages - 1,
            on_click=_next_page,
        )

    st.progress(min(1.0, (st.session_state.grid_page + 1) / total_pages))

    with st.spinner("Loading new page..."):
        page_start = st.session_state.grid_page * ipp
        page_data = review_data.iloc[page_start : page_start + ipp]

        _poor_img_comments = (
            fr[fr["FLAG"].isin(["Image Stretched", "Image Blurry", "Poor images"])]
            .set_index("ProductSetSid")["Comment"]
            .to_dict()
        )
        page_warnings = {}
        for _sid in page_data["PRODUCT_SET_SID"].astype(str):
            _comment = _poor_img_comments.get(_sid, "")
            _warns = []
            if _comment:
                _cl = _comment.lower()
                if "stretched" in _cl or "tall" in _cl:
                    _warns.append("Tall (Screenshot?)")
                if "stretched" in _cl or "wide" in _cl:
                    _warns.append("Wide Aspect")
                if (
                    "blurry" in _cl
                    or "low res" in _cl
                    or "resolution" in _cl
                    or "small" in _cl
                ):
                    _warns.append("Low Resolution")

            _row_fr = fr[fr["ProductSetSid"].astype(str) == _sid]
            if not _row_fr.empty:
                _flag = _row_fr.iloc[0]["FLAG"]
                if _flag and _flag not in ("Approved", "Manual review"):
                    _warns.append(_flag)

            _zip_index = st.session_state.get("_zip_sid_index")
            if _zip_index is not None and _sid in _zip_index.index:
                _zrow = _zip_index.loc[_sid]
                if hasattr(_zrow, "iloc") and hasattr(_zrow, "shape") and len(_zrow.shape) == 2:
                    _zrow = _zrow.iloc[0]
                _zip_status_cols = st.session_state.get("_zip_status_cols", [])
                _zip_prefetch_map = st.session_state.get("_zip_prefetch_map", {})
                for _zcol in _zip_status_cols:
                    if str(_zrow.get(_zcol, "")).lower() == "rejected":
                        _zflag = _zip_prefetch_map.get(_zcol, _zcol.replace("_Status", "").replace("_", " ").title())
                        if _zflag not in _warns:
                            _warns.append(_zflag)

            if _warns:
                page_warnings[_sid] = list(dict.fromkeys(_warns))

        seller_trust = {}
        if not fr.empty and "SELLER_NAME" in fr.columns:
            _stats = fr.groupby("SELLER_NAME")["Status"].value_counts(normalize=True).unstack().fillna(0)
            if "Rejected" in _stats.columns:
                seller_trust = (_stats["Rejected"] * 100).round(1).to_dict()

        _prefetch_cache_key = f"prefetch_{st.session_state.grid_page}_{len(review_data)}_{ipp}"
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
                for url in review_data.iloc[p_start : p_start + ipp]["MAIN_IMAGE"].astype(
                    str
                ):
                    url = url.strip().replace("http://", "https://", 1)
                    if url.startswith("https") and url not in seen_urls:
                        seen_urls.add(url)
                        prefetch_urls.append(url)
            st.session_state[_prefetch_cache_key] = prefetch_urls
        else:
            prefetch_urls = st.session_state[_prefetch_cache_key]

        rejected_state = {
            sid.strip(): st.session_state[f"quick_rej_reason_{sid.strip()}"]
            for sid in page_data["PRODUCT_SET_SID"].astype(str)
            if st.session_state.get(f"quick_rej_{sid.strip()}")
        }

        for _sid_raw in page_data.get(
            "PRODUCT_SET_SID", page_data.get("ProductSetSid", pd.Series())
        ).astype(str):
            _sid = _sid_raw.strip()
            if _sid in poor_img_rej_sids and _sid not in rejected_state:
                _row_fr = fr[fr["ProductSetSid"].astype(str).str.strip() == _sid]
                if not _row_fr.empty:
                    _flag = str(_row_fr.iloc[0]["FLAG"])
                    _comment = str(_row_fr.iloc[0]["Comment"])
                    if "Brand Image Check" in _flag or "Brand Image Check" in _comment:
                        rejected_state[_sid] = "Brand Image Check"
                    else:
                        rejected_state[_sid] = "Poor images"
                else:
                    rejected_state[_sid] = "Poor images"

        cols_per_row = st.session_state.get("grid_cols_per_row", 5)
        skeleton_html = (
            """
    <style>
      .sk-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
      .sk-card{border-radius:12px;overflow:hidden;background:#f3f4f6;height:260px;
               animation:pulse 1.4s ease-in-out infinite}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
      progress { width: 100%; height: 6px; border: none; border-radius: 4px; margin-bottom: 15px; }
      progress::-webkit-progress-bar { background-color: #ffe5cc; border-radius: 4px; }
      progress::-webkit-progress-value { background-color: #f97316; border-radius: 4px; }
      progress::-moz-progress-bar { background-color: #f97316; border-radius: 4px; }
    </style>
    <div style="font-family: sans-serif; color: #f97316; font-size: 14px; font-weight: bold; margin-bottom: 5px;">Loading Page...</div>
    <progress></progress>
    <div class="sk-grid">
    """
            + "".join(['<div class="sk-card"></div>'] * 12)
            + "</div>"
        )

        placeholder = st.empty()
        placeholder.html(skeleton_html)

        grid_html = build_fast_grid_html(
            page_data=page_data,
            flags_mapping=support_files.get("flags_mapping", {}),
            country=st.session_state.get("selected_country", "Kenya"),
            page_warnings=page_warnings,
            rejected_state=rejected_state,
            cols_per_row=cols_per_row,
            poor_img_sids=poor_img_rej_sids,
            prefetch_urls=prefetch_urls,
            scroll_to_top=scroll_top_flag,
            show_images=st.session_state.get("show_images", True),
            seller_trust=seller_trust,
            support_files=support_files,
            curr_sort=curr_sort,
            curr_flag=curr_flag,
        )

    placeholder.empty()
    with st.container(key=f"grid_iframe_pg_{st.session_state.get('grid_page', 0)}"):
        st.iframe(grid_html, height=750)
    st.markdown("---")

    pg_cols_bot = st.columns([1, 2, 1], vertical_alignment="bottom", gap="small")
    with pg_cols_bot[0]:
        st.button(
            "⬅ Prev",
            key="prev_bot",
            use_container_width=True,
            disabled=st.session_state.get("grid_page", 0) == 0,
            on_click=_prev_page,
        )
    with pg_cols_bot[1]:
        st.number_input(
            f"Page (of {total_pages})",
            min_value=1,
            max_value=max(1, total_pages),
            key="jump_bot",
            on_change=_jump_from_widget,
            args=("jump_bot",),
        )
    with pg_cols_bot[2]:
        st.button(
            "Next ➡",
            key="next_bot",
            use_container_width=True,
            disabled=st.session_state.grid_page >= total_pages - 1,
            on_click=_next_page,
        )

    st.progress(min(1.0, (st.session_state.grid_page + 1) / total_pages))

    if st.button("✖ Close Review", key="close_bot_fallback", use_container_width=True, type="secondary"):
        st.session_state.show_review_modal = False
        st.rerun()


@st.fragment
def render_manual_review_buttons(support_files):
    _fr = st.session_state.get("final_report", pd.DataFrame())
    if _fr.empty or st.session_state.get("file_mode") == "post_qc":
        return

    if not _fr.empty and "Status" in _fr.columns and "FLAG" in _fr.columns:
        _rej_count = int((
            (_fr["Status"] == "Rejected") &
            (_fr["FLAG"].astype(str).str.contains(r"\(Prefetched\)", na=False, case=False))
        ).sum())
    else:
        _rej_count = 0

    _audit_label = (
        f"Targeted Audit  ({_rej_count:,} rejected)"
        if _rej_count
        else "Targeted Audit"
    )

    has_zip_in_fr = not _fr.empty and "Is_Zip" in _fr.columns and _fr["Is_Zip"].any()
    has_zip = has_zip_in_fr or st.session_state.get("no_computation_zip", False) or not st.session_state.get("zip_qc_results", pd.DataFrame()).empty

    st.markdown("---")

    if has_zip:
        c1, c2, c3 = st.columns([2, 1, 1], gap="medium")
        with c1:
            st.header(_t("manual_review"), anchor=False)
            st.caption(
                "Use **Start Visual Review** to manually flip through products one by one, "
                "or **Targeted Audit** to review all rejections grouped by flag type."
            )
        with c2:
            if st.button(
                "Start Visual Review",
                type="primary",
                width='stretch',
                icon=":material/pageview:",
                key="btn_visual_review_main",
            ):
                st.session_state.show_review_modal = True
                st.session_state.show_targeted_audit_modal = False
        with c3:
            if st.button(
                _audit_label,
                type="secondary",
                width='stretch',
                icon=":material/ads_click:",
                key="btn_targeted_audit_main",
            ):
                st.session_state.show_targeted_audit_modal = True
                st.session_state.show_review_modal = False
    else:
        c1, c2 = st.columns([3, 1], gap="medium")
        with c1:
            st.header(_t("manual_review"), anchor=False)
            st.caption(
                "Use **Start Visual Review** to manually flip through products one by one."
            )
        with c2:
            if st.button(
                "Start Visual Review",
                type="primary",
                width='stretch',
                icon=":material/pageview:",
                key="btn_visual_review_main",
            ):
                st.session_state.show_review_modal = True
                st.session_state.show_targeted_audit_modal = False

    if st.session_state.get("show_targeted_audit_modal", False):
        targeted_audit_modal(support_files)
    elif st.session_state.get("show_review_modal", False):
        visual_review_modal(support_files)


@st.fragment
def render_image_grid(support_files):
    if (
        st.session_state.get("final_report", pd.DataFrame()).empty
        or st.session_state.get("file_mode") == "post_qc"
    ):
        return

    _warm_urls = st.session_state.get("_grid_warm_urls", [])
    if _warm_urls:
        _preload_tags = "\n".join(
            f'<link rel="preload" as="image" href="{url}" referrerpolicy="no-referrer">'
            for url in _warm_urls[:100]
        )
        st.markdown(
            f"<div style='display:none'>{_preload_tags}</div>", unsafe_allow_html=True
        )


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
    data = st.session_state.all_data_map

    if st.session_state.get("all_data_rows") is None:
        if "_data_filtered_ref" in st.session_state:
            st.session_state.all_data_rows = st.session_state._data_filtered_ref
        elif "current_sig_hash" in st.session_state:
            _fname = f"{st.session_state.current_sig_hash}_data_rows.parquet"
            st.session_state.all_data_rows = load_df_parquet(_fname)

    all_rows = st.session_state.get("all_data_rows", data)
    app_df = fr[fr["Status"] == "Approved"]
    rej_df = fr[fr["Status"] == "Rejected"]
    c_code = country_validator.code
    date_str = datetime.now().strftime("%Y-%m-%d")
    reasons_df = support_files.get("reasons", pd.DataFrame())

    st.markdown("---")
    st.header(_t("download_reports"), anchor=False)
    st.caption("Export QC results in Excel or ZIP format")

    def _gen_override_report(log):
        import io
        df = pd.DataFrame(log) if log else pd.DataFrame(
            columns=["Timestamp", "SID", "Product Name", "Rejection Flag",
                     "Seller", "AI Reason", "Action"]
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        return buf, f"{c_code}_Audit_Overrides_{date_str}.csv", "text/csv"

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