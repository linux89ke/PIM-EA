import re
import pandas as pd
import streamlit as st

# ── Check taxonomy ────────────────────────────────────────────────────────────
# Every check is read directly from the file's own Status/Reason column pair.
# "Prohibited" is no longer its own check — the file itself reports it as a
# reason *type* under Category (or under the pre-QC Skip step), so it lives
# there instead of being guessed at via a keyword list.
CHECK_ORDER = [
    "skip", "duplicate", "category", "color", "warranty", "variation", "fda",
    "title_language", "name_brand", "image_quality", "image_extraction",
    "ai_caption", "brand_image",
]

CHECK_LABELS = {
    "skip": "Pre-QC Skip Reasons",
    "duplicate": "Duplicate Products",
    "category": "Category Match",
    "color": "Color",
    "warranty": "Warranty",
    "variation": "Variation",
    "fda": "FDA / Regulatory Documents",
    "title_language": "Title Language / Weight",
    "name_brand": "Product Name ↔ Brand Name",
    "image_quality": "Image Quality",
    "image_extraction": "Image Extraction Errors",
    "ai_caption": "AI Product Caption Errors",
    "brand_image": "Brand Detected On Image",
}

try:
    from custom_country_rules import apply_kenya_book_rule
except ImportError:
    def apply_kenya_book_rule(*args, **kwargs): return None

# (status_column, reason_column) as they actually appear in the file, per check.
_CHECK_COLUMNS = {
    "category": ("Category_Check_Status", "Category_Check_Rejection_Reason"),
    "color": ("Color_Check_Status", "Color_Rejection_Reason"),
    "warranty": ("Warranty_Check_Status", "Warranty_Rejection_Reason"),
    "variation": ("Variation_Check_Status", "Variation_Rejection_Reason"),
    "fda": ("FDA_Check_Status", "FDA_Rejection_Reason"),
    "title_language": ("Title_Language_Check_Status", "Title_Language_Check_Reason"),
    "name_brand": ("Product Name_Brand Name_Status", "Product name_Brand name_rejection reason"),
    "image_quality": ("Image_Quality_Check_Status", "Image_Quality_Check_Reason"),
    "brand_image": ("Brand_Image_Check_Status", "Brand_Image_Check_Reason"),
}


def diagnose_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Returns a table showing, for every check: whether its expected
    Status/Reason columns exist in `data`, AND what actual status values are
    present in that column. A column can exist and still produce zero
    results if the status text doesn't match what the evaluator expects
    (e.g. 'Fail' instead of 'Rejected') — this makes that visible directly
    instead of it silently looking like the check has no findings at all."""
    rows = []
    for check_key, (status_col, reason_col) in _CHECK_COLUMNS.items():
        status_found = status_col in data.columns
        reason_found = reason_col in data.columns
        if status_found:
            vals = data[status_col].dropna().astype(str).str.strip()
            vals = vals[vals != ""]
            sample = ", ".join(f"{v} ({c})" for v, c in vals.value_counts().head(6).items())
        else:
            sample = ""
        rows.append({
            "Check": CHECK_LABELS[check_key],
            "Status Column": status_col,
            "Column Found?": "✅" if status_found else "❌ MISSING",
            "Actual Status Values Present": sample or "(no non-empty values found)",
            "Reason Column": reason_col,
            "Reason Col Found?": "✅" if reason_found else "❌ MISSING",
        })
    for key, col in (("skip", "QC_Skip_Reason"), ("duplicate", "Duplicate_Flag")):
        found = col in data.columns
        rows.append({
            "Check": CHECK_LABELS[key], "Status Column": col,
            "Column Found?": "✅" if found else "❌ MISSING",
            "Actual Status Values Present": "", "Reason Column": "", "Reason Col Found?": "",
        })
    return pd.DataFrame(rows)

# Substring → canonical reason-type label, checked in order, per check.
# Every distinct wording the file actually produces gets its own label —
# nothing gets merged into a generic bucket unless it truly matches nothing.
_REASON_PATTERNS = {
    "category": [
        ("prohibited", "Prohibited Category"),
        ("inactive", "Inactive Category"),
        ("manual review", "Flagged For Manual Review"),
        ("suggests sibling category", "AI Suggests Sibling Category"),
        ("rejects with", "Overlapping Category Path"),
        ("suggests a different category", "AI Suggests Different Category"),
        ("suggests a better category", "AI Suggests Different Category"),
    ],
    "fda": [
        ("therapeutic/medical device", "Medical Device Requires FDA Registration"),
        ("infant oral-contact", "Infant Feeding Tool Requires FDA Registration"),
        ("regulation registration number", "FDA Documentation Missing"),
    ],
    "color": [
        ("color not filled in", "Color Missing But Inferable From Title"),
        ("color is required", "Color Missing"),
    ],
    "warranty": [
        ("confirm the product warranty", "Warranty Field Empty"),
    ],
    "variation": [
        ("variation is mandatory", "Variation Field Empty"),
    ],
    "name_brand": [
        ("high-end brand", "High-End Brand Counterfeit Suspected"),
        ("generic brand is not allowed", "Generic Brand Not Allowed (Fashion)"),
        ("repeated in product name", "Brand Name Repeated In Title"),
        ("inspired/alternative perfume", "Inspired/Alternative Perfume Brand Not Accepted"),
    ],
    "title_language": [
        ("must include quantity", "Missing Quantity/Weight In Title"),
        ("not in english", "Title Not In English"),
    ],
    "brand_image": [
        ("brand mismatch", "Brand Mismatch (Image vs Listing)"),
        ("misspelling", "Brand Misspelling (Needs Correction)"),
    ],
}

_SKIP_PATTERNS = [
    ("prohibited", "Prohibited Category"),
    ("inactive", "Inactive Category"),
    ("sex/adult toy", "Adult/Sex Toy Product"),
    ("tester products", "Tester Product"),
    ("manual review", "High-End Brand Manual Review"),
]

# Default verdict per reason type. None means "run the file's own mandatory-
# attribute rule to confirm/deny it" rather than assuming — these are the
# checks we can actually re-verify from the data (color/warranty/variation/
# FDA/weight all have a deterministic mandatory-or-not answer per category).
_REASON_VERDICT = {
    "Prohibited Category": "True Rejection",
    "Inactive Category": "True Rejection",
    "Flagged For Manual Review": "Needs Manual Review",
    "AI Suggests Sibling Category": "Needs Manual Review",
    "Overlapping Category Path": "Needs Manual Review",
    "AI Suggests Different Category": "Needs Manual Review",
    "Medical Device Requires FDA Registration": "True Rejection",
    "Infant Feeding Tool Requires FDA Registration": "True Rejection",
    "FDA Documentation Missing": None,
    "Color Missing But Inferable From Title": None,
    "Color Missing": None,
    "Warranty Field Empty": None,
    "Variation Field Empty": None,
    "High-End Brand Counterfeit Suspected": "True Rejection",
    "Generic Brand Not Allowed (Fashion)": "True Rejection",
    "Brand Name Repeated In Title": "True Rejection",
    "Inspired/Alternative Perfume Brand Not Accepted": "True Rejection",
    "Missing Quantity/Weight In Title": None,
    "Title Not In English": "True Rejection",
    "Brand Mismatch (Image vs Listing)": "Needs Manual Review",
    "Brand Misspelling (Needs Correction)": "Needs Manual Review",
}

_STATUS_TO_VERDICT_BASE = {"rejected": "True Rejection", "review": "Needs Manual Review",
                            "manual review": "Needs Manual Review"}


def _clean(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


def _match(check_key: str, reason_text: str):
    """Returns (reason_type_label, is_error). Any reason mentioning 'error'
    is pulled out as an AI Error rather than treated as a genuine finding —
    it means the check itself failed, not that it found something."""
    r = _clean(reason_text)
    if not r:
        return "Reason Not Provided", False
    if "error" in r.lower():
        return "AI Error", True
    for substr, label in _REASON_PATTERNS.get(check_key, []):
        if substr in r.lower():
            return label, False
    return "Other / Unclassified Reason", False


def _match_skip(reason_text: str):
    r = _clean(reason_text)
    if not r:
        return "Other Skip Reason", False
    if "error" in r.lower():
        return "AI Error", True
    for substr, label in _SKIP_PATTERNS:
        if substr in r.lower():
            return label, False
    return "Other Skip Reason", False


def _match_extraction_error(status_text: str) -> str:
    """Classifies the raw Image_Extraction_Status error text into a
    canonical reason type, so 'IncompleteRead' failures and outright
    'Connection failed' failures show up as separate rows rather than one
    generic bucket."""
    s = status_text.lower()
    if "incompleteread" in s or "connection broken" in s:
        return "Incomplete Read (Connection Broken)"
    if "connection failed" in s:
        return "Connection Failed"
    if "timeout" in s or "timed out" in s:
        return "Timeout"
    return "Other Extraction Error"


def _match_caption_error(caption_text: str) -> str:
    """AI_Product_Caption can contain an embedded error (the image-captioning
    step failed) instead of an actual caption — classifies which kind of
    failure it was, same idea as the extraction-error parser above."""
    s = caption_text.lower()
    if "invalid_image_url" in s or "timeout while downloading" in s:
        return "Invalid/Timeout Image URL"
    if "status_code': 500" in s or "internal" in s:
        return "Server Error"
    if "status_code': 429" in s or "rate limit" in s:
        return "Rate Limited"
    return "Other Caption Error"


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
    try:
        with open("colors.txt", "r", encoding="utf-8") as f:
            colors = [line.strip().lower() for line in f if line.strip()]
            return sorted(colors, key=len, reverse=True)
    except Exception:
        return []


@st.cache_data
def load_color_set() -> set:
    """Returns colors.txt as a lowercase set for O(1) membership checks."""
    return set(load_colors())


@st.cache_data
def get_color_regex():
    colors = load_colors()
    if not colors:
        return None
    pattern = r"\b(" + "|".join(re.escape(c) for c in colors) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


@st.cache_data
def load_qc_excel(country_code: str):
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


# ── Rule-based re-verification for the checks that have a deterministic answer ─
def _verify(check_key: str, rec: dict, rule: dict, weights: set, color_re) -> str:
    """Only called for reason types marked None in _REASON_VERDICT — actually
    re-derives whether the file's own mandatory-attribute rule agrees."""
    cat_code = _clean(rec.get("CATEGORY_CODE"))
    name = _clean(rec.get("NAME"))

    if check_key == "fda":
        req = _clean(rule.get("FDA Documents", "Mandatory")).lower()
        return "True Rejection" if req != "no need" else "False Rejection"

    if check_key == "color":
        req = _clean(rule.get("Color", "Mandatory")).lower()
        color_val = _clean(rec.get("COLOR"))
        if req == "no need":
            return "False Rejection"
        # Any value that starts with 'multi' is a valid multicolor declaration
        if re.match(r"^multi", color_val.lower()):
            return "False Rejection"
        # Column must be filled AND the value must be a recognised color
        if not color_val:
            return "True Rejection"
        valid_colors = load_color_set()
        if valid_colors and color_val.lower() not in valid_colors:
            # Value present but not a real color (e.g. 'Ascorbic') — rejection was correct
            return "True Rejection"
        return "False Rejection"

    if check_key == "warranty":
        req = _clean(rule.get("Warranty", "Mandatory")).lower()
        return "True Rejection" if req != "no need" else "False Rejection"

    if check_key == "variation":
        req = _clean(rule.get("Variation", "Mandatory")).lower()
        var_val = _clean(rec.get("VARIATION")) or _clean(rec.get("LIST_VARIATIONS"))
        var_count = _clean(rec.get("COUNT_OF_EXISTING_VARIATIONS")) or _clean(rec.get("COUNT_VARIATIONS"))
        has_var = bool(var_val or (var_count and var_count not in ("0",)))
        if has_var:
            return "False Rejection"
        return "True Rejection" if req != "no need" else "False Rejection"

    if check_key == "title_language":
        return "True Rejection" if cat_code in weights else "False Rejection"

    return "Needs Manual Review"


def _verify_false_approval(check_key: str, rec: dict, rule: dict, weights: set, color_re) -> str:
    """Returns a reason-type label if an approved row actually violates the
    file's own mandatory rule, else '' (nothing wrong)."""
    cat_code = _clean(rec.get("CATEGORY_CODE"))
    name = _clean(rec.get("NAME"))

    if check_key == "fda":
        if _clean(rule.get("FDA Documents", "")).lower() == "mandatory" and not _clean(rec.get("FDA")):
            return "FDA Documentation Missing"
    elif check_key == "color":
        if _clean(rule.get("Color", "")).lower() == "mandatory":
            color_val = _clean(rec.get("COLOR"))
            # Only the COLOR column counts — COLOR_FAMILY alone or color mentioned in the
            # product name/title is NOT sufficient for a valid color declaration.
            if not color_val:
                return "Color Missing"
    elif check_key == "warranty":
        if _clean(rule.get("Warranty", "")).lower() == "mandatory" and not _clean(rec.get("PRODUCT_WARRANTY")):
            return "Warranty Field Empty"
    elif check_key == "variation":
        if _clean(rule.get("Variation", "")).lower() == "mandatory":
            var_val = _clean(rec.get("VARIATION")) or _clean(rec.get("LIST_VARIATIONS"))
            var_count = _clean(rec.get("COUNT_OF_EXISTING_VARIATIONS")) or _clean(rec.get("COUNT_VARIATIONS"))
            if not var_val and (not var_count or var_count == "0"):
                return "Variation Field Empty"
    elif check_key == "title_language":
        if cat_code in weights and not re.search(r"\d+\s*(g|kg|ml|l|oz|lb|pcs|pieces)\b", name, re.IGNORECASE):
            return "Missing Quantity/Weight In Title"
    return ""


# ── Row/context builder ───────────────────────────────────────────────────────
# Every field here is something a reviewer would actually want to look at to
# double-check the specific finding — not just the bare minimum. Pulled
# straight from columns that exist in the file (including the AI-derived
# ones like Color_AI_Normalized, Category_Match_Score, Brand_Detected_On_Product).
def _context_columns(check_key: str, rec: dict) -> dict:
    ctx = {}
    if check_key == "category":
        ctx["Initial Category Path"] = _clean(rec.get("Initial_Category_Path"))
        ctx["Suggested Categories"] = _clean(rec.get("Suggested_Categories"))
        ctx["Top1 Category"] = _clean(rec.get("Top1_Category"))
        ctx["Category Match Score"] = _clean(rec.get("Category_Match_Score"))
        ctx["Top1 Score"] = _clean(rec.get("Top1_Score"))
        ctx["AI Product Caption"] = _clean(rec.get("AI_Product_Caption"))
    elif check_key == "color":
        ctx["Color"] = _clean(rec.get("COLOR"))
        ctx["Color Family"] = _clean(rec.get("COLOR_FAMILY"))
        ctx["Color (AI Normalized)"] = _clean(rec.get("Color_AI_Normalized"))
    elif check_key == "warranty":
        ctx["Warranty"] = _clean(rec.get("PRODUCT_WARRANTY"))
        ctx["Warranty Type"] = _clean(rec.get("WARRANTY_TYPE"))
        ctx["Warranty Duration"] = _clean(rec.get("WARRANTY_DURATION"))
        ctx["Warranty Address"] = _clean(rec.get("WARRANTY_ADDRESS"))
    elif check_key == "variation":
        ctx["Variation"] = _clean(rec.get("VARIATION")) or _clean(rec.get("LIST_VARIATIONS"))
        ctx["Existing Variation Count"] = _clean(rec.get("COUNT_OF_EXISTING_VARIATIONS")) or _clean(rec.get("COUNT_VARIATIONS"))
    elif check_key == "fda":
        ctx["FDA"] = _clean(rec.get("FDA"))
    elif check_key == "title_language":
        pass  # Product Name (already in base row) is the whole subject of this check
    elif check_key == "name_brand":
        ctx["Brand"] = _clean(rec.get("BRAND"))
        ctx["Brand Detected On Product"] = _clean(rec.get("Brand_Detected_On_Product"))
    elif check_key == "image_quality":
        ctx["Image"] = _clean(rec.get("MAIN_IMAGE"))
        ctx["Image Filename"] = _clean(rec.get("Image_Filename"))
    elif check_key == "brand_image":
        ctx["Listed Brand"] = _clean(rec.get("BRAND"))
        ctx["Brand Detected On Product"] = _clean(rec.get("Brand_Detected_On_Product"))
        ctx["Image"] = _clean(rec.get("MAIN_IMAGE"))
    elif check_key == "image_extraction":
        ctx["Image"] = _clean(rec.get("MAIN_IMAGE"))
    elif check_key == "ai_caption":
        ctx["Image"] = _clean(rec.get("MAIN_IMAGE"))
    elif check_key == "duplicate":
        pass  # Seller is now in base row
    return ctx


def _base_row(sid: str, check_key: str, rec: dict) -> dict:
    row = {
        "ProductSetSid": sid,
        "Check": check_key,
        "Product Name": _clean(rec.get("NAME")),
        "Seller": _clean(rec.get("SELLER_NAME")),
        "Category": _clean(rec.get("CATEGORY")),
    }
    row.update(_context_columns(check_key, rec))
    return row


_NEEDED = [
    "PRODUCT_SET_SID", "CATEGORY_CODE", "NAME", "BRAND", "COLOR", "CATEGORY",
    "COLOR_FAMILY", "Color_AI_Normalized",
    "PRODUCT_WARRANTY", "WARRANTY_TYPE", "WARRANTY_DURATION", "WARRANTY_ADDRESS",
    "COUNT_VARIATIONS", "LIST_VARIATIONS", "VARIATION", "COUNT_OF_EXISTING_VARIATIONS",
    "FDA", "MAIN_IMAGE", "Image_Filename",
    "Initial_Category_Path", "Suggested_Categories", "Top1_Category",
    "Category_Match_Score", "Top1_Score", "AI_Product_Caption",
    "Brand_Detected_On_Product",
    "Image_Extraction_Status", "QC_Skip_Reason", "Duplicate_Flag", "SELLER_NAME",
] + [col for pair in _CHECK_COLUMNS.values() for col in pair]


# ── Image extraction errors (pipeline-level, independent of QC decisions) ────
def get_image_extraction_errors(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty or "Image_Extraction_Status" not in data.columns:
        return pd.DataFrame()
    status = data["Image_Extraction_Status"].astype(str)
    mask = status.str.strip().str.lower().ne("successful") & status.str.strip().ne("")
    cols = [c for c in ["PRODUCT_SET_SID", "NAME", "MAIN_IMAGE", "Image_Extraction_Status"] if c in data.columns]
    return data.loc[mask, cols].copy()


# ── The unified evaluator ──────────────────────────────────────────────────────
def _append_approved_findings(rows: list, sid: str, check_key: str, rec: dict, rule: dict,
                               weights: set, color_re, reason: str) -> None:
    """Shared logic for an item that has no rejection recorded for this check
    (either status == 'Approved', or no status column exists and the reason
    field is blank). Checks for a missed mandatory-attribute violation, a
    brand-misspelling note, or an embedded error — used whether or not a
    status column is present."""
    if reason and "error" in reason.lower():
        rows.append({**_base_row(sid, check_key, rec), "Reason Type": "AI Error",
                     "Verdict": "AI Error", "Detail": reason})
        return
    if reason and (reason.lower().startswith("approved (spelling note)") or "misspelling" in reason.lower()):
        rows.append({**_base_row(sid, check_key, rec),
                     "Reason Type": "Brand Misspelling (Needs Correction)",
                     "Verdict": "Needs Manual Review", "Detail": reason})
        return
    fa_label = _verify_false_approval(check_key, rec, rule, weights, color_re)
    if fa_label:
        rows.append({**_base_row(sid, check_key, rec), "Reason Type": fa_label,
                     "Verdict": "False Approval",
                     "Detail": "Approved, but the file's own mandatory-attribute "
                               "rule for this category says it shouldn't have been."})


def evaluate_all_checks(data: pd.DataFrame, country_code: str) -> pd.DataFrame:
    """
    Walks every product in `data` and reads each of the file's own nine
    per-check Status/Reason column pairs directly — no guessing from a
    summarized flag. Every distinct reason string is split into its own
    labeled Reason Type (e.g. 'Title Not In English' is never merged with
    'Missing Quantity/Weight In Title'), Duplicate_Flag and QC_Skip_Reason
    become their own top-level checks, and any reason mentioning 'error'
    is broken out as a separate 'AI Error' flag rather than a real finding.

    Returns one row per (product, check, reason) with columns:
    ProductSetSid, Check, Product Name, Category, [check-specific fields],
    Reason Type, Verdict, Detail.

    Verdict is one of: True Rejection, False Rejection, False Approval,
    Needs Manual Review, AI Error, Skipped, Duplicate.
    """
    if data.empty:
        return pd.DataFrame(columns=["ProductSetSid", "Check", "Product Name", "Category",
                                      "Reason Type", "Verdict", "Detail"])

    weights = load_weight_categories()
    color_re = get_color_regex()
    qc_rules = load_qc_excel(country_code)
    cols = [c for c in _NEEDED if c in data.columns]
    records = data[cols].to_dict("records")
    status_cols_present = {status_col for status_col, _ in _CHECK_COLUMNS.values() if status_col in data.columns}

    rows = []
    for rec in records:
        sid = _clean(rec.get("PRODUCT_SET_SID"))
        cat_code = _clean(rec.get("CATEGORY_CODE"))
        rule = qc_rules.get(cat_code, {})

        # ── Pre-QC skip ───────────────────────────────────────────────────
        skip_reason = _clean(rec.get("QC_Skip_Reason"))
        if skip_reason:
            label, is_error = _match_skip(skip_reason)
            rows.append({**_base_row(sid, "skip", rec), "Reason Type": label,
                         "Verdict": "AI Error" if is_error else "Skipped", "Detail": skip_reason})
            continue  # skipped products never went through the 9 checks

        # ── Duplicate ────────────────────────────────────────────────────
        dup_flag = _clean(rec.get("Duplicate_Flag"))
        if dup_flag:
            rows.append({**_base_row(sid, "duplicate", rec),
                         "Reason Type": "Duplicate Product (Same Seller + Name)",
                         "Verdict": "Duplicate", "Detail": dup_flag})

        # ── Image extraction failures (infrastructure, not a QC decision,
        # but still surfaced as its own check so it's counted alongside
        # every other AI Error and shown consistently in the same tables/
        # report instead of a disconnected side-list) ────────────────────
        extraction_status = _clean(rec.get("Image_Extraction_Status"))
        if extraction_status and extraction_status.lower() != "successful":
            label = _match_extraction_error(extraction_status)
            rows.append({**_base_row(sid, "image_extraction", rec), "Reason Type": label,
                         "Verdict": "AI Error", "Detail": extraction_status})

        # ── AI_Product_Caption can contain an embedded error instead of an
        # actual caption (the captioning step itself failed, e.g. it
        # couldn't download the image) — surfaced the same way.
        caption_text = _clean(rec.get("AI_Product_Caption"))
        if caption_text and caption_text.lower().startswith("ai error"):
            label = _match_caption_error(caption_text)
            rows.append({**_base_row(sid, "ai_caption", rec), "Reason Type": label,
                         "Verdict": "AI Error", "Detail": caption_text})

        # ── The file's own nine checks ───────────────────────────────────
        for check_key, (status_col, reason_col) in _CHECK_COLUMNS.items():
            has_status_col = status_col in status_cols_present
            status = _clean(rec.get(status_col)).lower() if has_status_col else ""
            reason = _clean(rec.get(reason_col))
            
            # ── Custom Country Rules ─────────────────────────────────────────
            if check_key == "category" and country_code == "KE":
                custom_row = apply_kenya_book_rule(sid, rec, has_status_col, status, reason, _base_row)
                if custom_row:
                    rows.append(custom_row)
                    continue

            # ── Warranty contradiction check ──────────────────────────────
            # If PRODUCT_WARRANTY actually has a value, a rejection for this
            # check is wrong no matter what the reason text says or whether
            # the mandatory-rule sheet loaded — the file's own data
            # contradicts its own decision. Always worth flagging as its
            # own distinct issue rather than folding into "Warranty Field
            # Empty" (which would be misleading here).
            if check_key == "warranty":
                warranty_val = _clean(rec.get("PRODUCT_WARRANTY"))
                is_rejection_like = (
                    (has_status_col and status in ("rejected", "review", "manual review"))
                    or (not has_status_col and reason and "error" not in reason.lower())
                )
                if warranty_val and is_rejection_like:
                    rows.append({**_base_row(sid, "warranty", rec),
                                 "Reason Type": "Warranty Present But Rejected",
                                 "Verdict": "False Rejection",
                                 "Detail": f"Rejected for missing warranty, but PRODUCT_WARRANTY "
                                           f"= '{warranty_val}'. Original reason: "
                                           f"{reason or '(none provided)'}"})
                    continue

            # ── Title Language / Volume contradiction check ──────────────────
            # If the product was rejected for missing volume but the sophisticated
            # regex extracts volume from the NAME successfully, it's a False Rejection.
            if check_key == "title_language":
                is_rejection_like = (
                    (has_status_col and status in ("rejected", "review", "manual review"))
                    or (not has_status_col and reason and "error" not in reason.lower())
                )
                if is_rejection_like:
                    name_val = _clean(rec.get("NAME"))
                    if name_val:
                        import re
                        pat = re.compile(
                            r"\b\d+(?:\.\d+)?\s*(?:[a-z]{1,20}\s*){0,3}"
                            r"(?:kg|kgs|g|gm|gms|grams|mg|mcg|ml|l|ltr|liter|litres|litre|cl|oz|ounce|ounces|lb|lbs|m"
                            r"|tablets?|tabs?|capsules?|caps?|sachets?|count|ct|sticks?|iu"
                            r"|tea\s*bags?|teabags?|bags?|softgels?|lozenges?|gummies|gummy|vials?|ampoules?|tubes?"
                            r"|pieces?|pcs|pack|packs|pairs?|rolls?|sheets?|wipes?|pods?|units?|serves?|servings?|vegan\s+pieces?"
                            r"|dozens?|box|boxes|set|sets|bundle|bundles|lot|lots|collection|kit|kits)"
                            r"|\b\d+[\u0027\u2019]?s\b"
                            r"|\b(?:a\s+)?dozen\b"
                            r"|\b(?:pack|box|set|bundle|lot)\s+of\s+\d+\b"
                            r"|\bper\s+(?:kg|kgs?|g|gm|grams?|mg|mcg|ml|l|ltr|oz|lb)\b"
                            r"|\d+\s*(?:\xc2\xb5g|\xce\xbcg|\xb5g|\u00b5g|\u03bcg|mcg|µg|μg)",
                            re.IGNORECASE,
                        )
                        if pat.search(name_val):
                            rows.append({**_base_row(sid, "title_language", rec),
                                         "Reason Type": "Volume Present But Rejected",
                                         "Verdict": "False Rejection",
                                         "Detail": f"Rejected for missing volume/weight in title, but valid volume format detected in NAME: '{name_val}'. Original reason: {reason or '(none provided)'}"})
                            continue

            if has_status_col and status:
                # ── Driven by an explicit status column ──────────────────
                if status == "skipped":
                    continue

                if status == "rejected":
                    label, is_error = _match(check_key, reason)
                    if is_error:
                        verdict = "AI Error"
                    else:
                        verdict = _REASON_VERDICT.get(label)
                        if verdict is None:
                            verdict = _verify(check_key, rec, rule, weights, color_re)
                    rows.append({**_base_row(sid, check_key, rec), "Reason Type": label,
                                 "Verdict": verdict, "Detail": reason or "No reason text provided."})

                elif status in ("review", "manual review"):
                    label, is_error = _match(check_key, reason) if reason else ("Flagged For Manual Review", False)
                    rows.append({**_base_row(sid, check_key, rec), "Reason Type": label,
                                 "Verdict": "AI Error" if is_error else "Needs Manual Review",
                                 "Detail": reason or "Flagged for manual review by the pipeline."})

                elif status == "approved":
                    _append_approved_findings(rows, sid, check_key, rec, rule, weights, color_re, reason)

                else:
                    # Status value we don't recognize (e.g. 'Fail', 'Flagged',
                    # 'Needs Fix') — surface it rather than silently dropping
                    # the row, so a wording mismatch is visible instead of
                    # making the whole check appear to have zero findings.
                    rows.append({**_base_row(sid, check_key, rec),
                                 "Reason Type": f"Unrecognized Status: '{rec.get(status_col)}'",
                                 "Verdict": "Needs Manual Review",
                                 "Detail": reason or "This check reported a status value the "
                                                      "audit tool doesn't recognize yet."})

            else:
                # ── No usable status column — infer purely from whether a
                # reason was written. Empty reason = nothing was flagged for
                # this check (treat as approved and check for a missed
                # mandatory-attribute violation); non-empty reason = the
                # check found something, classify it from the text itself.
                if not reason:
                    _append_approved_findings(rows, sid, check_key, rec, rule, weights, color_re, "")
                    continue

                if "error" in reason.lower():
                    rows.append({**_base_row(sid, check_key, rec), "Reason Type": "AI Error",
                                 "Verdict": "AI Error", "Detail": reason})
                    continue

                label, is_error = _match(check_key, reason)
                if is_error:
                    verdict = "AI Error"
                elif "manual review" in reason.lower() or label == "Flagged For Manual Review":
                    verdict = "Needs Manual Review"
                else:
                    verdict = _REASON_VERDICT.get(label)
                    if verdict is None:
                        verdict = _verify(check_key, rec, rule, weights, color_re)
                rows.append({**_base_row(sid, check_key, rec), "Reason Type": label,
                             "Verdict": verdict, "Detail": reason})

    if not rows:
        return pd.DataFrame(columns=["ProductSetSid", "Check", "Product Name", "Category",
                                      "Reason Type", "Verdict", "Detail"])
    return pd.DataFrame(rows)