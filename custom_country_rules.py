import pandas as pd

def _clean_text(val) -> str:
    if val is None:
        return ""
    s = str(val)
    return "" if s.strip().lower() in ("nan", "none") else s.strip()


def _kenya_book_mask(rec) -> bool:
    name_lower = _clean_text(rec.get("NAME")).lower()
    brand_lower = _clean_text(rec.get("BRAND")).lower()
    cat_lower = _clean_text(rec.get("CATEGORY")).lower()
    cat_code_lower = _clean_text(rec.get("CATEGORY_CODE")).lower()

    is_book_product = any(
        k in name_lower for k in ("author", "book by")
    ) or any(k in brand_lower for k in ("author", "book by"))
    is_book_cat = "book" in cat_lower or "book" in cat_code_lower
    return is_book_product and not is_book_cat


def check_kenya_book_category(data: pd.DataFrame) -> pd.DataFrame:
    """
    Return Kenya book-mismatch rows as a normal QC DataFrame.

    This is used by the main validation pipeline so Kenya books that are
    mis-categorized are rejected, not just reported in the audit dialog.
    """
    required = {"PRODUCT_SET_SID", "NAME", "BRAND", "CATEGORY", "CATEGORY_CODE"}
    if data.empty or not required.issubset(data.columns):
        return pd.DataFrame(columns=data.columns)

    d = data.copy()
    name_lower = d["NAME"].astype(str).str.lower()
    brand_lower = d["BRAND"].astype(str).str.lower()
    cat_lower = d["CATEGORY"].astype(str).str.lower()
    cat_code_lower = d["CATEGORY_CODE"].astype(str).str.lower()

    is_book_product = (
        name_lower.str.contains(r"\bauthor\b|\bbook by\b", regex=True, na=False)
        | brand_lower.str.contains(r"\bauthor\b|\bbook by\b", regex=True, na=False)
    )
    is_book_cat = cat_lower.str.contains("book", na=False) | cat_code_lower.str.contains("book", na=False)
    flagged = d[is_book_product & ~is_book_cat].copy()
    if flagged.empty:
        return pd.DataFrame(columns=data.columns)

    flagged["Comment_Detail"] = flagged.apply(
        lambda row: (
            f"Product name/brand indicates a book, but category "
            f"'{_clean_text(row.get('CATEGORY'))}' is not a book category."
        ),
        axis=1,
    )
    return flagged.drop_duplicates(subset=["PRODUCT_SET_SID"])


def apply_kenya_book_rule(sid: str, rec: dict, has_status_col: bool, status: str, reason: str, base_row_fn) -> dict | None:
    """
    If the product is a book (has 'author' or 'book by' in NAME or BRAND)
    but its CATEGORY is not a book category, it should be flagged as Wrong Category.
    If it's already rejected for category by the system, we do nothing (let normal QC handle it).
    """
    is_rejection_like = (
        (has_status_col and status in ("rejected", "review", "manual review"))
        or (not has_status_col and reason and "error" not in reason.lower())
    )
    
    if is_rejection_like:
        return None
        
    if _kenya_book_mask(rec):
        row = base_row_fn(sid, "category", rec)
        row.update({
            "Reason Type": "Wrong Category (Book mapped incorrectly)",
            "Verdict": "False Approval",
            "Detail": f"Product name/brand indicates a book, but category '{_clean_text(rec.get('CATEGORY'))}' is not a book category. Originally: {reason or 'approved'}"
        })
        return row
        
    return None

import re
import os
import streamlit as st

@st.cache_data(ttl=3600)
def load_health_beauty_codes() -> set:
    try:
        from openpyxl import load_workbook
        cat_wb = load_workbook('category_map.xlsx', read_only=True)
        cat_ws = cat_wb.active
        cat_rows = list(cat_ws.iter_rows(values_only=True))
        cat_wb.close()
        
        # Column 1 = code, Column 2 = path
        hb_codes = set()
        for r in cat_rows[1:]:
            if r[1] and r[2]:
                path = str(r[2]).strip()
                if path.startswith("Health") and "Beauty" in path:
                    hb_codes.add(str(int(r[1])))
        return hb_codes
    except Exception:
        return set()


# ALLOWLIST: only category path sections that are relevant for KEBS skin-product checking.
# This is MUCH more precise than an exclusion list — only skin care / body beauty / cosmetics
# categories are checked. Hair care, health care, oral care, vision, sexual wellness, 
# supplements, tools, accessories etc. are all naturally excluded.
_KEBS_ALLOWED_PATH_SECTIONS = (
    # Core skin & body care
    "skin care",
    "body care",
    "bathing & skin care",
    "bath & body",
    "body & bath",
    "bath, body & accessories",
    "soaps",
    "cleansers",
    "toners",
    "moisturizers",
    "lotions",
    "face care",
    "body lotion",
    "body scrub",
    "exfoliants",
    "sunscreen",
    "sunblock",
    "sun care",
    "sun protection",
    # Cosmetics / makeup
    "makeup",
    "cosmetics",
    "face makeup",
    "lip makeup",
    "eye makeup",
    "bb & cc cream",
    "foundation",
    "concealer",
    "primer",
    # Fragrances
    "fragrances",
    "perfumes",
    # Men's grooming (skin-related)
    "men's grooming",
    # Dermocosmetics
    "dermocosmetics",
    # Beauty & personal care top-level section
    "beauty & personal care",
    # Baby skin / body care (specifically bathing and skin)
    "bathing & skin care",
    "diapering",
    # Kid's beauty
    "kid's beauty",
    # Deodorants (can contain lightening agents)
    "deodorants",
    "antiperspirants",
    # Shave & hair removal (relevant — depilatory products can have banned ingredients)
    "shave & hair removal",
)


@st.cache_data(ttl=3600)
def load_kebs_hb_codes() -> set:
    """
    Load category codes for KEBS skin-product check using an ALLOWLIST approach.
    Only includes beauty/skin/body care, cosmetics, fragrances, and baby skin care categories.
    All other H&B sections (hair care, oral care, health care, supplements, 
    vision, sexual wellness, tools, etc.) are naturally excluded.
    """
    try:
        from openpyxl import load_workbook
        cat_wb = load_workbook('category_map.xlsx', read_only=True)
        cat_ws = cat_wb.active
        cat_rows = list(cat_ws.iter_rows(values_only=True))
        cat_wb.close()

        kebs_codes = set()
        allowed_lower = tuple(s.lower() for s in _KEBS_ALLOWED_PATH_SECTIONS)

        for r in cat_rows[1:]:
            if not r[1] or not r[2]:
                continue
            path = str(r[2]).strip().lower()

            # Must be in Health & Beauty OR Baby Products (for baby skin care)
            is_hb = path.startswith("health & beauty") or path.startswith("health and beauty")
            is_baby = path.startswith("baby")

            if not (is_hb or is_baby):
                continue

            # Check if any allowed section appears in the path
            if any(section in path for section in allowed_lower):
                try:
                    kebs_codes.add(str(int(r[1])))
                except (ValueError, TypeError):
                    pass

        return kebs_codes
    except Exception:
        return set()


@st.cache_data(ttl=3600)
def load_kebs_banned_products():
    """
    Returns a dict of:
      brand_lower -> {"reason": str, "product_types": [str], "products": [str]}
    where product_types are the known product type words from the KEBS list for that brand.
    """
    xlsx_path = "kebs_banned_products (1).xlsx"
    csv_path = "kebs_banned_products (1).csv"
    try:
        if os.path.exists(xlsx_path):
            df = pd.read_excel(xlsx_path, sheet_name=0, dtype=str)
        elif os.path.exists(csv_path):
            df = pd.read_csv(csv_path, encoding="cp1252", dtype=str)
        else:
            return {}

        df = df.fillna('')
        brand_map = {}  # brand_lower -> {reason, product_names_lower}
        for _, row in df.iterrows():
            reason = str(row.get('Reason for Ban', '')).strip()
            brand = str(row.get('Brand', '')).strip()
            product = str(row.get('Product Name', '')).strip()
            if not brand or brand.lower() in ('nan', 'none', ''):
                continue
            bl = brand.lower()
            if bl not in brand_map:
                brand_map[bl] = {"reason": reason, "products": []}
            if product and product.lower() not in ('nan', 'none', ''):
                brand_map[bl]["products"].append(product.lower())

        return brand_map
    except Exception:
        return {}


# Skin-lightening product type indicators — if a product name contains any of these
# it is considered a skin-lightening product and will be checked against the brand list.
_SKIN_PRODUCT_TYPES = re.compile(
    r"\b(cream|creme|lotion|gel|soap|serum|milk|toner|bleach|balm|oil|lemon|lait|lait corp|"
    r"fade|skin|lightening|whitening|brightening|complexion|beauty|medicated|clarifying|"
    r"moisturiz|moisturis|body|face|treatment)\b",
    re.IGNORECASE,
)


def check_kebs_banned_products(data: pd.DataFrame) -> pd.DataFrame:
    """
    Two-step check:
    1. Check if the product's BRAND (or first word of NAME) matches a brand in the KEBS banned list.
    2. If brand matches, confirm the product NAME contains a skin-product type word
       (cream, lotion, gel, soap, etc.) to ensure it is actually the type of product that is banned.
    Only flag when BOTH conditions are met.
    """
    required = {"PRODUCT_SET_SID", "NAME", "BRAND", "CATEGORY_CODE"}
    if data.empty or not required.issubset(data.columns):
        return pd.DataFrame(columns=data.columns)

    brand_map = load_kebs_banned_products()
    if not brand_map:
        return pd.DataFrame(columns=data.columns)

    hb_codes = load_kebs_hb_codes()
    if not hb_codes:
        return pd.DataFrame(columns=data.columns)

    d = data.copy()

    # Filter for H&B categories only
    cat_mask = d["CATEGORY_CODE"].astype(str).str.strip().isin(hb_codes)
    target = d[cat_mask].copy()
    if target.empty:
        return pd.DataFrame(columns=data.columns)

    target["_brand_l"] = target["BRAND"].fillna("").str.strip().str.lower()
    target["_name_l"] = target["NAME"].fillna("").str.strip().str.lower()
    # First word of NAME as secondary brand identifier
    target["_name_first_word"] = target["_name_l"].str.split().str[0].fillna("")

    # Build a sorted list of KEBS brands, longest first, for pattern matching
    all_brands = sorted(brand_map.keys(), key=len, reverse=True)
    # Only match brands that are meaningful (>=4 chars) to avoid generic single words
    # BUT for multi-word brands always include them
    meaningful_brands = [b for b in all_brands if len(b) >= 4 or " " in b]
    if not meaningful_brands:
        return pd.DataFrame(columns=data.columns)

    brand_pattern = re.compile(
        r"(?<![\\w])(?:" + "|".join(re.escape(b) for b in meaningful_brands) + r")(?![\\w])",
        re.IGNORECASE,
    )

    results = []
    for idx, row in target.iterrows():
        brand_val = row["_brand_l"]
        name_val = row["_name_l"]
        first_word = row["_name_first_word"]

        # --- Step 1: Does BRAND or first word of NAME match a KEBS brand? ---
        matched_brand = None

        # Check product BRAND field first (exact or contained)
        for b in meaningful_brands:
            pat = re.compile(r"(?<!\w)" + re.escape(b) + r"(?!\w)", re.IGNORECASE)
            if pat.search(brand_val):
                matched_brand = b
                break

        # If no match in BRAND, check first word of NAME
        if not matched_brand and first_word and len(first_word) >= 4:
            for b in meaningful_brands:
                if first_word == b:
                    matched_brand = b
                    break

        if not matched_brand:
            continue

        # --- Step 2: Does the product NAME contain a skin-product type word? ---
        if not _SKIN_PRODUCT_TYPES.search(name_val):
            # The brand matched but the product doesn't look like a skin product — skip
            continue

        # Build the rejection comment
        entry = brand_map.get(matched_brand, {})
        reason = entry.get("reason", "Banned substance.")
        comment = (
            f"{reason} Please contact Jumia Seller Support and raise a claim to confirm "
            f"whether this product is eligible for listing. See https://www.kebs.org/banned-products/"
        )

        result_row = row.copy()
        result_row["FLAG"] = "1000033 - Keywords in your content/ Product name / description has been blacklisted"
        result_row["Comment_Detail"] = comment
        results.append(result_row)

    if not results:
        return pd.DataFrame(columns=data.columns)

    flagged = pd.DataFrame(results)
    return flagged.drop_duplicates(subset=["PRODUCT_SET_SID"])




def apply_kebs_banned_rule(sid: str, rec: dict, has_status_col: bool, status: str, reason: str, base_row_fn) -> dict | None:
    is_rejection_like = (
        (has_status_col and status in ("rejected", "review", "manual review"))
        or (not has_status_col and reason and "error" not in reason.lower())
    )
    
    if is_rejection_like:
        return None

    brand_map = load_kebs_banned_products()
    if not brand_map:
        return None

    hb_codes = load_kebs_hb_codes()
    if not hb_codes:
        return None

    cat_code = str(rec.get("CATEGORY_CODE", "")).strip()
    if cat_code not in hb_codes:
        return None

    brand_val = str(rec.get("BRAND", "")).strip().lower()
    name_val = str(rec.get("NAME", "")).strip().lower()
    first_word = name_val.split()[0] if name_val.split() else ""

    # --- Step 1: Does BRAND or first word of NAME match a KEBS brand? ---
    all_brands = sorted(brand_map.keys(), key=len, reverse=True)
    meaningful_brands = [b for b in all_brands if len(b) >= 4 or " " in b]
    matched_brand = None

    for b in meaningful_brands:
        pat = re.compile(r"(?<!\w)" + re.escape(b) + r"(?!\w)", re.IGNORECASE)
        if pat.search(brand_val):
            matched_brand = b
            break

    if not matched_brand and first_word and len(first_word) >= 4:
        for b in meaningful_brands:
            if first_word == b:
                matched_brand = b
                break

    if not matched_brand:
        return None

    # --- Step 2: Does the product NAME contain a skin-product type word? ---
    if not _SKIN_PRODUCT_TYPES.search(name_val):
        return None

    entry = brand_map.get(matched_brand, {})
    ban_reason = entry.get("reason", "Banned substance.")
    detail = (
        f"{ban_reason} Please contact Jumia Seller Support and raise a claim to confirm "
        f"whether this product is eligible for listing. See https://www.kebs.org/banned-products/"
    )
    row = base_row_fn(sid, "kebs", rec)
    row.update({
        "Reason Type": "KEBS Banned Products",
        "Verdict": "False Approval",
        "Detail": detail
    })
    return row


@st.cache_data(ttl=3600)
def load_kebs_fda_keywords():
    if not os.path.exists("kebs_banned_products (1).xlsx"):
        return None
    try:
        df = pd.read_excel("kebs_banned_products (1).xlsx", sheet_name="Sheet1")
        if "FDA keywords" not in df.columns:
            return None
        kws = df["FDA keywords"].dropna().astype(str).str.strip().tolist()
        kws = [k for k in kws if k and k.lower() not in ("nan", "none")]
        if not kws:
            return None
        kws = sorted(kws, key=len, reverse=True)
        pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(k) for k in kws) + r")(?!\w)",
            re.IGNORECASE,
        )
        return pattern
    except Exception:
        return None


def check_kebs_fda(data: pd.DataFrame) -> pd.DataFrame:
    required = {"PRODUCT_SET_SID", "NAME", "BRAND", "CATEGORY_CODE"}
    if data.empty or not required.issubset(data.columns):
        return pd.DataFrame(columns=data.columns)

    pattern = load_kebs_fda_keywords()
    if not pattern:
        return pd.DataFrame(columns=data.columns)

    hb_codes = load_kebs_hb_codes()
    if not hb_codes:
        return pd.DataFrame(columns=data.columns)

    d = data.copy()
    
    # Filter for H&B categories
    cat_mask = d["CATEGORY_CODE"].astype(str).str.strip().isin(hb_codes)
    target = d[cat_mask]
    if target.empty:
        return pd.DataFrame(columns=data.columns)

    text_to_search = (target["BRAND"].fillna("") + " " + target["NAME"].fillna("")).str.lower()
    match_mask = text_to_search.str.contains(pattern, na=False)
    
    flagged = target[match_mask].copy()
    if flagged.empty:
        return pd.DataFrame(columns=data.columns)

    def _build_comment(row_text: str) -> str:
        matches = pattern.findall(row_text)
        kw = matches[0].upper() if matches else "FDA Controlled Substance"
        return f"Product name or brand contains '{kw}' which requires FDA approval. Please contact Jumia Seller Support to confirm eligibility."

    flagged["FLAG"] = "FDA"
    flagged["Comment_Detail"] = text_to_search[match_mask].apply(_build_comment)
    
    return flagged.drop_duplicates(subset=["PRODUCT_SET_SID"])


