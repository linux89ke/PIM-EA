import pandas as pd

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
        
    def _clean(val):
        if val is None: return ""
        s = str(val)
        return "" if s.strip().lower() in ("nan", "none") else s

    name_lower = _clean(rec.get("NAME")).lower()
    brand_lower = _clean(rec.get("BRAND")).lower()
    cat_lower = _clean(rec.get("CATEGORY")).lower()
    cat_code_lower = _clean(rec.get("CATEGORY_CODE")).lower()
    
    is_book_product = any(k in name_lower for k in ["author", "book by"]) or any(k in brand_lower for k in ["author", "book by"])
    is_book_cat = "book" in cat_lower or "book" in cat_code_lower
    
    if is_book_product and not is_book_cat:
        row = base_row_fn(sid, "category", rec)
        row.update({
            "Reason Type": "Wrong Category (Book mapped incorrectly)",
            "Verdict": "False Approval",
            "Detail": f"Product name/brand indicates a book, but category '{_clean(rec.get('CATEGORY'))}' is not a book category. Originally: {reason or 'approved'}"
        })
        return row
        
    return None
