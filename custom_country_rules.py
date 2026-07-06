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
