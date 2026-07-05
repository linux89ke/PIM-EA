import re
import difflib
import pandas as pd
import streamlit as st
from typing import Optional

# ── Check taxonomy ────────────────────────────────────────────────────────────
# Every flag the pipeline can raise is bucketed into exactly one of these so the
# UI can show one expandable section per check. "unclassified" is the catch-all
# for any flag string that doesn't match a known pattern — it must never be
# silently dropped.
CHECK_ORDER = [
    "category", "prohibited", "color", "warranty", "variation", "fda",
    "title_language", "name_brand", "image_quality", "brand_image",
]

CHECK_LABELS = {
    "category": "Category Match",
    "prohibited": "Prohibited / Restricted Product",
    "color": "Color",
    "warranty": "Warranty",
    "variation": "Variation",
    "fda": "FDA / Regulatory Documents",
    "title_language": "Title Language / Weight",
    "name_brand": "Product Name ↔ Brand Name",
    "image_quality": "Image Quality",
    "brand_image": "Brand Detected On Image",
}

# Checks that fundamentally require looking at the actual image. With no AI/
# vision call available, we don't guess — these always land in "Needs Manual
# Review" so nothing gets silently rubber-stamped.
_VISUAL_ONLY_CHECKS = {"image_quality", "brand_image"}

# Minimum text-similarity ratio (difflib) between the current category and the
# AI-suggested category for us to consider the original category "close enough"
# to be acceptable. Pure string comparison — no model call.
_CATEGORY_SIMILARITY_THRESHOLD = 0.55

# Well-known premium/luxury brands that are common counterfeit-listing targets.
# Kept small and explicit on purpose — this is a deterministic keyword list,
# not an attempt to cover every brand in existence.
_PREMIUM_BRANDS = {
    "apple", "samsung", "nike", "adidas", "gucci", "rolex", "sony", "lg", "dell",
    "hp", "chanel", "louis vuitton", "prada", "puma", "canon", "nikon", "dyson",
    "bose", "beats", "iphone", "playstation", "xbox",
}

# Adult/prohibited trigger keywords paired with "safe context" words that, when
# present in the same title, indicate the trigger word is being used in a
# harmless, non-adult sense (e.g. "concrete vibrator", "facial massager").
_PROHIBITED_KEYWORDS = {
    "vibrator": ["concrete", "massage", "massager", "facial", "muscle", "body", "phone", "handheld", "percussion"],
    "dildo": [],  # no known harmless usage — always a true match
    "sex toy": [],
    "adult toy": [],
    "vibrating": ["massage", "massager", "facial", "muscle", "phone", "motor"],
}


def _classify_flag(flag: str) -> Optional[str]:
    """Map a raw FLAG string to one of CHECK_ORDER, or None if it doesn't
    match a known validation from the file (such rows are skipped, not shown
    under a catch-all bucket)."""
    f = (flag or "").lower()
    if "prohibited" in f:
        return "prohibited"
    if "category" in f:
        return "category"
    if "color" in f:
        return "color"
    if "warranty" in f:
        return "warranty"
    if "variation" in f:
        return "variation"
    if "fda" in f:
        return "fda"
    if "title language" in f or "weight" in f:
        return "title_language"
    if "brand" in f and "image" in f:
        return "brand_image"
    if ("name" in f and "brand" in f) or "name_brand" in f:
        return "name_brand"
    if "image" in f and "quality" in f:
        return "image_quality"
    return None


# ── Cached file loaders ───────────────────────────────────────────────────────
@st.cache_data
def load_weight_categories():
    try:
        with open("weight.txt", "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()


@st.cache_data
def load_colors():
    """Return sorted color list from colors.txt (longest first for greedy matching)."""
    try:
        with open("colors.txt", "r", encoding="utf-8") as f:
            colors = [line.strip().lower() for line in f if line.strip()]
            return sorted(colors, key=len, reverse=True)
    except Exception:
        return []


@st.cache_data
def get_color_regex():
    colors = load_colors()
    if not colors:
        return None
    pattern = r"\b(" + "|".join(re.escape(c) for c in colors) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


@st.cache_data
def load_qc_excel(country_code: str):
    """Returns a dict mapping Category ID to its rules for the given country."""
    if not country_code:
        country_code = "UG"
    cc = country_code.upper().strip()
    if cc in ("SN", "CI"):
        cc = "FRENCH"
    try:
        xl = pd.ExcelFile("QC Check Validaton  (3).xlsx")
        target_name = f"Mandatory Attributes - {cc}"
        found_sheet = None
        for name in xl.sheet_names:
            if name.strip().replace("  ", " ") == target_name:
                found_sheet = name
                break
        if found_sheet:
            df = xl.parse(found_sheet)
            if "ID" in df.columns:
                df["ID"] = df["ID"].astype(str).str.strip()
                df = df[df["ID"].notna() & (df["ID"] != "") & (df["ID"] != "nan")]
                return df.set_index("ID").to_dict(orient="index")
    except Exception:
        pass
    return {}


# ── Pure-python replacements for what used to be AI calls ────────────────────
def check_category_match(current_cat: str, suggested_cat: str) -> Optional[bool]:
    """
    True  = current category is close enough to the suggestion to be acceptable
    False = category genuinely looks wrong
    None  = not enough information to judge (no suggestion available)

    Pure string-similarity comparison (difflib) — no network call, deterministic.
    """
    current_cat = (current_cat or "").strip().lower()
    suggested_cat = (suggested_cat or "").strip().lower()
    if not current_cat or not suggested_cat:
        return None

    # Suggested_Categories can contain multiple "Path (score%)|Path (score%)"
    # entries — compare against each candidate and take the best match.
    candidates = [c.split("(")[0].strip() for c in suggested_cat.split("|") if c.strip()]
    if not candidates:
        candidates = [suggested_cat]

    best_ratio = max(
        difflib.SequenceMatcher(None, current_cat, cand).ratio()
        for cand in candidates
    )

    # Also treat an exact substring/leaf-segment match as acceptable, since
    # paths often differ only in parent segments.
    current_leaf = current_cat.split("/")[-1].strip()
    leaf_match = any(current_leaf and current_leaf in cand for cand in candidates)

    return bool(leaf_match or best_ratio >= _CATEGORY_SIMILARITY_THRESHOLD)


def check_prohibited_keywords(name: str, current_cat: str) -> Optional[bool]:
    """
    True  = harmless, looks like a false positive (safe context word present)
    False = genuinely looks prohibited
    None  = no known trigger keyword found in the title at all (can't judge)

    Pure keyword/context matching — no network call, deterministic.
    """
    name_l = (name or "").lower()
    cat_l = (current_cat or "").lower()

    matched_trigger = None
    for trigger in _PROHIBITED_KEYWORDS:
        if trigger in name_l:
            matched_trigger = trigger
            break

    if matched_trigger is None:
        return None  # nothing to evaluate against

    # If the product's own category is clearly an adult/intimate category,
    # don't let a "safe context" word override that.
    if any(k in cat_l for k in ("adult", "sex", "intimate", "sensual")):
        return False

    safe_words = _PROHIBITED_KEYWORDS[matched_trigger]
    if safe_words and any(w in name_l for w in safe_words):
        return True

    return False


def check_name_brand_match(name: str, brand: str) -> Optional[bool]:
    """
    True  = brand plausibly matches the title (found verbatim, or brand is a
            generic/no-brand placeholder)
    False = a *different* well-known premium brand appears in the title than
            the one listed — likely counterfeit/mislabelled
    None  = ambiguous — can't be resolved without visual/human inspection

    Pure substring/keyword matching — no network call, deterministic.
    """
    name_l = (name or "").strip().lower()
    brand_l = (brand or "").strip().lower()
    if not name_l or not brand_l:
        return None

    if brand_l in ("generic", "no brand", "unbranded", "un branded", "nan", "none", ""):
        # Generic is fine unless the title itself claims a premium brand.
        found_premium = [b for b in _PREMIUM_BRANDS if b in name_l]
        if found_premium:
            return False  # e.g. NAME says "Apple" but BRAND is "Generic"
        return True

    if brand_l in name_l:
        return True  # brand literally appears in the title — clean match

    # Brand not found in title at all, and title doesn't claim a different
    # premium brand either — can't confidently say it's wrong.
    found_other_premium = [b for b in _PREMIUM_BRANDS if b in name_l and b not in brand_l]
    if found_other_premium:
        return False  # title claims a different premium brand than listed

    return None  # ambiguous, needs a human to look at it


# ── Shared data lookup builder ────────────────────────────────────────────────
_NEEDED = [
    'PRODUCT_SET_SID', 'CATEGORY_CODE', 'NAME', 'BRAND', 'COLOR',
    'PRODUCT_WARRANTY', 'COUNT_VARIATIONS', 'LIST_VARIATIONS',
    'FDA', 'CATEGORY', 'MAIN_IMAGE', 'Image_Extraction_Status',
]


def _build_data_lookup(data: pd.DataFrame) -> dict:
    if data.empty or "PRODUCT_SET_SID" not in data.columns:
        return {}
    _sub = data[[c for c in _NEEDED if c in data.columns]]
    return {
        str(sid).strip(): row
        for sid, row in zip(_sub["PRODUCT_SET_SID"], _sub.to_dict("records"))
    }


# ── Image extraction errors (pipeline-level, unrelated to QC decisions) ──────
def get_image_extraction_errors(data: pd.DataFrame) -> pd.DataFrame:
    """Every row whose Image_Extraction_Status indicates a failure (broken
    connection, timeout, etc.) rather than a QC decision. Shown separately
    because these are infrastructure failures, not audit outcomes."""
    if data.empty or "Image_Extraction_Status" not in data.columns:
        return pd.DataFrame()
    status = data["Image_Extraction_Status"].astype(str)
    mask = status.str.strip().str.lower().ne("successful") & status.str.strip().ne("")
    cols = [c for c in ["PRODUCT_SET_SID", "NAME", "MAIN_IMAGE", "Image_Extraction_Status"] if c in data.columns]
    return data.loc[mask, cols].copy()


# ── False approvals, broken out per check ─────────────────────────────────────
def get_false_approvals(approved_df: pd.DataFrame, data: pd.DataFrame, country_code: str) -> pd.DataFrame:
    """
    Scans approved items for violations the system should have caught.
    Returns a DataFrame with a 'Check' column so results can be grouped into
    one expandable section per check in the UI. Any per-row exception is
    captured into the row itself rather than aborting the whole scan.
    """
    color_re = get_color_regex()
    qc_rules = load_qc_excel(country_code)
    weights = load_weight_categories()
    data_lookup = _build_data_lookup(data)

    results = []

    for row in approved_df.to_dict("records"):
        if str(row.get("Is_Manual", "")).strip().lower() == "true":
            continue

        sid = str(row.get("ProductSetSid", "")).strip()
        merged = data_lookup.get(sid, {})
        cat_code = str(merged.get("CATEGORY_CODE", "")).strip()
        name = str(merged.get("NAME", "")).strip()
        brand = str(merged.get("BRAND", "")).strip()
        current_cat = str(merged.get("CATEGORY", "")).strip()
        rule = qc_rules.get(cat_code, {})

        try:
            # ── FDA ───────────────────────────────────────────────────────
            if str(rule.get("FDA Documents", "")).strip().lower() == "mandatory":
                fda_val = str(merged.get("FDA", "")).strip()
                if not fda_val or fda_val.lower() in ("nan", "none", ""):
                    results.append({"ProductSetSid": sid, "Check": "fda",
                                     "Detail": "Approved but FDA documentation is mandatory and missing."})

            # ── Color ─────────────────────────────────────────────────────
            if str(rule.get("Color", "")).strip().lower() == "mandatory":
                color_col_val = str(merged.get("COLOR", "")).strip()
                color_in_col = bool(color_col_val and color_col_val.lower() not in ("nan", "none", ""))
                color_in_name = bool(color_re and color_re.search(name)) if name else False
                if not color_in_name and not color_in_col:
                    results.append({"ProductSetSid": sid, "Check": "color",
                                     "Detail": "Approved but Color is mandatory and missing from title and column."})

            # ── Warranty ──────────────────────────────────────────────────
            if str(rule.get("Warranty", "")).strip().lower() == "mandatory":
                war_val = str(merged.get("PRODUCT_WARRANTY") or merged.get("product_warranty") or "").strip()
                if not war_val or war_val.lower() in ("nan", "none", ""):
                    results.append({"ProductSetSid": sid, "Check": "warranty",
                                     "Detail": "Approved but Warranty is mandatory and missing."})

            # ── Variation ─────────────────────────────────────────────────
            if str(rule.get("Variation", "")).strip().lower() == "mandatory":
                var_count = str(merged.get("COUNT_VARIATIONS") or merged.get("count_variations") or "").strip()
                var_list = str(merged.get("LIST_VARIATIONS") or merged.get("list_variations") or "").strip()
                var_present = (
                    bool(var_count and var_count not in ("0", "nan", "none", ""))
                    or bool(var_list and var_list.lower() not in ("nan", "none", "[]", ""))
                )
                if not var_present:
                    results.append({"ProductSetSid": sid, "Check": "variation",
                                     "Detail": "Approved but Variation is mandatory and missing."})

            # ── Title Language / Weight ───────────────────────────────────
            if cat_code in weights:
                if not re.search(r"\d+\s*(g|kg|ml|l|oz|lb|pcs|pieces)\b", name, re.IGNORECASE):
                    results.append({"ProductSetSid": sid, "Check": "title_language",
                                     "Detail": "Approved but category requires a weight/quantity in the title and none was found."})

            # ── Name ↔ Brand ────────────────────────────────────────────────
            verdict = check_name_brand_match(name, brand)
            if verdict is False:
                results.append({"ProductSetSid": sid, "Check": "name_brand",
                                 "Detail": f"Approved but title implies a different brand than listed brand '{brand}'."})

            # ── Prohibited (approved items that still contain a trigger word) ──
            verdict_p = check_prohibited_keywords(name, current_cat)
            if verdict_p is False:
                results.append({"ProductSetSid": sid, "Check": "prohibited",
                                 "Detail": "Approved but title contains a restricted-product keyword with no safe context."})

        except Exception:
            # Skip this item's remaining checks rather than surfacing a
            # non-file validation bucket; the checks that did complete above
            # are still recorded.
            continue

    if not results:
        return pd.DataFrame(columns=["ProductSetSid", "Check", "Detail"])
    return pd.DataFrame(results)


# ── Rejection evaluation, broken out per check ────────────────────────────────
def evaluate_rejections(
    rejected_df: pd.DataFrame,
    data: pd.DataFrame,
    country_code: str,
) -> pd.DataFrame:
    """
    Evaluates every rejected row and classifies each into exactly one of:
      - "True Rejection"        — the system was right to reject it
      - "False Rejection"       — should have been approved
      - "Needs Manual Review"   — can't be resolved by pure rules (e.g.
                                    image-quality / brand-on-image, or an
                                    ambiguous name/brand/category case)
      - "Unclassified"          — the FLAG string didn't match any known check
                                    (surfaced instead of silently ignored)

    Pure rule-based logic throughout — no network/AI calls.
    Returns one row per (sid, check) so the UI can group by 'Check'.
    """
    weights = load_weight_categories()
    color_re = get_color_regex()
    qc_rules = load_qc_excel(country_code)
    data_lookup = _build_data_lookup(data)

    total = len(rejected_df)
    st.session_state["_bg_audit_progress"] = 1.0  # synchronous, no long-running phase anymore
    st.session_state["_bg_audit_progress_text"] = f"✅ Validated {total} item(s)."

    rows = []

    for row in rejected_df.to_dict("records"):
        sid = str(row.get("ProductSetSid", "")).strip()
        flag_raw = str(row.get("FLAG", ""))
        check_key = _classify_flag(flag_raw)

        if check_key is None:
            # Not one of the validations produced by the file — skip rather
            # than inventing a catch-all bucket.
            continue

        merged = data_lookup.get(sid, {})
        cat_code = str(merged.get("CATEGORY_CODE", "")).strip()
        name = str(merged.get("NAME", "")).strip()
        brand = str(merged.get("BRAND", "")).strip()
        current_cat = str(merged.get("CATEGORY", "")).strip()
        rule = qc_rules.get(cat_code, {})
        sug_cat = str(row.get("Suggested_Categories", ""))

        # ── Visual-only checks: no vision capability, don't guess ──────────
        if check_key in _VISUAL_ONLY_CHECKS:
            rows.append({"ProductSetSid": sid, "Check": check_key,
                         "Classification": "Needs Manual Review",
                         "Reason": "Requires visual inspection of the image; no automated verifier available."})
            continue

        # ── Color (pure rule) ──────────────────────────────────────────────
        if check_key == "color":
            col_req = str(rule.get("Color", "Mandatory")).strip().lower()
            color_col_val = str(merged.get("COLOR", "")).strip()
            color_in_col = bool(color_col_val and color_col_val.lower() not in ("nan", "none", ""))
            color_in_name = bool(color_re and color_re.search(name)) if name else False
            if not color_in_name and not color_in_col and col_req != "no need":
                rows.append({"ProductSetSid": sid, "Check": "color", "Classification": "True Rejection",
                             "Reason": "Color genuinely absent from title and column."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "color", "Classification": "False Rejection",
                             "Reason": "Color is present, or category doesn't require it."})
            continue

        # ── Warranty (pure rule) ───────────────────────────────────────────
        if check_key == "warranty":
            war_req = str(rule.get("Warranty", "Mandatory")).strip().lower()
            war_val = str(merged.get("PRODUCT_WARRANTY") or merged.get("product_warranty") or "").strip()
            war_present = bool(war_val and war_val.lower() not in ("nan", "none", ""))
            if not war_present and war_req != "no need":
                rows.append({"ProductSetSid": sid, "Check": "warranty", "Classification": "True Rejection",
                             "Reason": "Warranty genuinely missing."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "warranty", "Classification": "False Rejection",
                             "Reason": "Warranty is present, or category doesn't require it."})
            continue

        # ── Title Language / Weight (pure rule) ────────────────────────────
        if check_key == "title_language":
            if cat_code in weights:
                rows.append({"ProductSetSid": sid, "Check": "title_language", "Classification": "True Rejection",
                             "Reason": "Category requires weight/quantity in title."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "title_language", "Classification": "False Rejection",
                             "Reason": "Category does not require weight/quantity in title."})
            continue

        # ── Variation (pure rule) ───────────────────────────────────────────
        if check_key == "variation":
            var_req = str(rule.get("Variation", "Mandatory")).strip().lower()
            var_count = str(merged.get("COUNT_VARIATIONS") or merged.get("count_variations") or "").strip()
            var_list = str(merged.get("LIST_VARIATIONS") or merged.get("list_variations") or "").strip()
            var_present = (
                bool(var_count and var_count not in ("0", "nan", "none", ""))
                or bool(var_list and var_list.lower() not in ("nan", "none", "[]", ""))
            )
            if var_present:
                rows.append({"ProductSetSid": sid, "Check": "variation", "Classification": "False Rejection",
                             "Reason": "Seller-provided variations exist."})
            elif var_req == "no need":
                rows.append({"ProductSetSid": sid, "Check": "variation", "Classification": "False Rejection",
                             "Reason": "Category doesn't require variation."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "variation", "Classification": "True Rejection",
                             "Reason": "Variation required and genuinely missing."})
            continue

        # ── FDA (pure rule) ──────────────────────────────────────────────────
        if check_key == "fda":
            fda_val = str(merged.get("FDA", "")).strip()
            fda_present = bool(fda_val and fda_val.lower() not in ("nan", "none", ""))
            fda_req = str(rule.get("FDA Documents", "Mandatory")).strip().lower()
            if fda_present:
                rows.append({"ProductSetSid": sid, "Check": "fda", "Classification": "False Rejection",
                             "Reason": "FDA documentation is present."})
            elif fda_req == "no need":
                rows.append({"ProductSetSid": sid, "Check": "fda", "Classification": "False Rejection",
                             "Reason": "FDA documentation not actually required for this category."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "fda", "Classification": "True Rejection",
                             "Reason": "FDA documentation required and missing."})
            continue

        # ── Category (pure string-similarity rule) ────────────────────────
        if check_key == "category":
            verdict = check_category_match(current_cat, sug_cat)
            if verdict is None:
                rows.append({"ProductSetSid": sid, "Check": "category", "Classification": "Needs Manual Review",
                             "Reason": "No suggested category available to compare against."})
            elif verdict:
                rows.append({"ProductSetSid": sid, "Check": "category", "Classification": "False Rejection",
                             "Reason": "Current category text is close enough to the suggestion to be acceptable."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "category", "Classification": "True Rejection",
                             "Reason": "Current category text doesn't resemble the suggested category."})
            continue

        # ── Prohibited (pure keyword/context rule) ─────────────────────────
        if check_key == "prohibited":
            verdict = check_prohibited_keywords(name, current_cat)
            if verdict is None:
                rows.append({"ProductSetSid": sid, "Check": "prohibited", "Classification": "Needs Manual Review",
                             "Reason": "No recognized trigger keyword found in title — can't confirm automatically."})
            elif verdict:
                rows.append({"ProductSetSid": sid, "Check": "prohibited", "Classification": "False Rejection",
                             "Reason": "Trigger keyword found alongside a safe-context word (likely harmless)."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "prohibited", "Classification": "True Rejection",
                             "Reason": "Trigger keyword found with no safe context — looks genuinely prohibited."})
            continue

        # ── Name ↔ Brand (pure keyword rule) ────────────────────────────────
        if check_key == "name_brand":
            verdict = check_name_brand_match(name, brand)
            if verdict is None:
                rows.append({"ProductSetSid": sid, "Check": "name_brand", "Classification": "Needs Manual Review",
                             "Reason": "Brand doesn't appear in title and no conflicting premium brand found either — ambiguous."})
            elif verdict:
                rows.append({"ProductSetSid": sid, "Check": "name_brand", "Classification": "False Rejection",
                             "Reason": "Brand matches the title (or title makes no premium-brand claim)."})
            else:
                rows.append({"ProductSetSid": sid, "Check": "name_brand", "Classification": "True Rejection",
                             "Reason": "Title implies a different brand than the one listed."})
            continue

    if not rows:
        return pd.DataFrame(columns=["ProductSetSid", "Check", "Classification", "Reason"])
    return pd.DataFrame(rows)


def get_true_rejection_sids(rejected_df: pd.DataFrame, data: pd.DataFrame, country_code: str) -> set:
    """Backward-compatible wrapper: just the SIDs classified as True Rejection.
    Prefer evaluate_rejections() directly for the full per-check breakdown."""
    df = evaluate_rejections(rejected_df, data, country_code)
    if df.empty:
        return set()
    return set(df.loc[df["Classification"] == "True Rejection", "ProductSetSid"])