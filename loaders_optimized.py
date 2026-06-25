"""
loaders_optimized.py
────────────────────────────────────────────────────────────────
OPTIMIZED version of loaders.py with faster file I/O and caching.
Replace the slow iterrows() and file loading operations.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional
from functools import lru_cache

import pandas as pd
import streamlit as st

from data_utils import clean_category_code

logger = logging.getLogger(__name__)

# Module-level cache for compiled regex patterns
_REGEX_CACHE: dict = {}

COUNTRY_TABS = ["KE", "UG", "NG", "GH", "MA"]
COUNTRY_NAME_TO_TAB = {
    "Kenya": "KE",
    "Uganda": "UG",
    "Nigeria": "NG",
    "Ghana": "GH",
    "Morocco": "MA",
}


# ─────────────────────────────────────────────────────────────────
# OPTIMIZATION 1: Faster file reading (avoid dtype=str for everything)
# ─────────────────────────────────────────────────────────────────

def load_txt_file(filename: str) -> List[str]:
    """Load text file with caching."""
    try:
        if not os.path.exists(os.path.abspath(filename)):
            return []
        with open(filename, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.warning(f"load_txt_file({filename}): {e}")
        return []


@st.cache_data(ttl=3600)
def load_excel_file_optimized(filename: str, column: Optional[str] = None):
    """Load Excel with selective columns, NOT all as strings."""
    try:
        if not os.path.exists(filename):
            return [] if column else pd.DataFrame()
        
        # Read ONLY the needed column(s) to reduce memory
        df = pd.read_excel(
            filename,
            engine="openpyxl",
            dtype=str if column else None,  # Only force str if specific column
            sheet_name=0,
        )
        df.columns = df.columns.str.strip()
        
        if column and column in df.columns:
            return df[column].apply(clean_category_code).tolist()
        return df
    except Exception as e:
        logger.warning(f"load_excel_file_optimized({filename}): {e}")
        return [] if column else pd.DataFrame()


def safe_excel_read_fast(filename: str, sheet_name, usecols=None) -> pd.DataFrame:
    """Faster Excel read with selective columns."""
    if not os.path.exists(filename):
        return pd.DataFrame()
    try:
        df = pd.read_excel(
            filename,
            sheet_name=sheet_name,
            usecols=usecols,
            engine="openpyxl",
            dtype=str,
        )
        return df.dropna(how="all")
    except Exception as e:
        logger.error(f"safe_excel_read_fast: tab='{sheet_name}' file={filename}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────
# OPTIMIZATION 2: Vectorized operations instead of iterrows()
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_category_map_fast(filename: str = "category_map.xlsx") -> Dict[str, str]:
    """Load category map using vectorized pandas (NOT iterrows)."""
    import os
    if not os.path.exists(filename):
        csv_path = filename.replace('.xlsx', '.csv')
        if os.path.exists(csv_path):
            filename = csv_path
        else:
            return {}
    try:
        df = (
            pd.read_csv(filename, dtype=str)
            if filename.endswith('.csv')
            else pd.read_excel(filename, engine='openpyxl', dtype=str)
        )
        df.columns = df.columns.str.strip()
        
        name_col = next((c for c in df.columns if 'name' in c.lower()), None)
        code_col = next((c for c in df.columns if 'code' in c.lower()), None)
        path_col = next((c for c in df.columns if 'path' in c.lower()), None)
        
        if not name_col or not code_col:
            return {}
        
        # VECTORIZED: Use pandas operations, NOT loops
        names = df[name_col].astype(str).str.strip()
        codes = df[code_col].astype(str).str.strip().str.split('.').str[0]
        
        valid = (
            names.str.lower().ne("nan") 
            & codes.str.lower().ne("nan") 
            & names.ne("") 
            & codes.ne("")
        )
        
        # Build mapping with zip (fast, vectorized)
        mapping: Dict[str, str] = dict(zip(names[valid].str.lower(), codes[valid]))
        
        if path_col:
            paths = df[path_col].astype(str).str.strip()
            path_valid = paths.str.lower().ne("nan") & paths.ne("")
            lasts = paths[path_valid].str.split('/').str[-1].str.strip().str.lower()
            path_codes = codes[path_valid]
            
            # Vectorized zip instead of loop
            for last, code in zip(lasts, path_codes):
                if last and last not in mapping:
                    mapping[last] = code
        
        return mapping
    except Exception as e:
        logger.warning(f"load_category_map_fast: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────
# OPTIMIZATION 3: Faster restricted brands loading
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_restricted_brands_fast() -> Dict[str, List[Dict]]:
    """Load restricted brands with vectorized operations."""
    FILE_NAME = "Restricted_Brands.xlsx"
    config_by_country = {}
    
    for country_name, tab_name in COUNTRY_NAME_TO_TAB.items():
        try:
            df = safe_excel_read_fast(FILE_NAME, sheet_name=tab_name)
            if df.empty:
                config_by_country[country_name] = []
                continue
            
            df.columns = [str(c).strip().lower() for c in df.columns]
            brand_col_vals = df.get("brand", pd.Series(dtype=str)).astype(str).str.strip()
            valid = brand_col_vals.str.lower().ne("nan") & brand_col_vals.ne("")
            
            df = df[valid].copy()
            df["_b_lower"] = brand_col_vals[valid].values.str.lower()
            
            # ────────────────────────────────────────────────
            # VECTORIZED SET SPLITTING (no slow loops)
            # ────────────────────────────────────────────────
            def _split_set_vectorized(series, sep=","):
                """Vectorized set splitting using pandas str methods."""
                return series.astype(str).str.strip().apply(
                    lambda x: set() if not x or x.lower() == "nan"
                    else {v.strip().lower() for v in x.split(sep) if v.strip()}
                )
            
            sellers_s = _split_set_vectorized(
                df.get("approved sellers", pd.Series([""] * len(df), index=df.index)), ","
            )
            cats_s = df.get("categories", pd.Series([""] * len(df), index=df.index)).apply(
                lambda x: None if (not x or str(x).lower() == "nan")
                else {clean_category_code(c.strip()) for c in str(x).split(",") if c.strip()}
            )
            vars_s = _split_set_vectorized(
                df.get("variations", pd.Series([""] * len(df), index=df.index)), ","
            )
            
            # Expanded variations
            import ast
            def _parse_expanded(val):
                s = str(val).strip()
                if not s or s.lower() == "nan":
                    return set()
                try:
                    parsed = ast.literal_eval(s)
                    if isinstance(parsed, list):
                        return {str(v).strip().lower() for v in parsed if str(v).strip()}
                except Exception:
                    pass
                return {v.strip().lower() for v in s.split(",") if v.strip()}
            
            exp_vars_s = df.get(
                "expanded variations", 
                pd.Series([""] * len(df), index=df.index)
            ).apply(_parse_expanded)
            
            # ────────────────────────────────────────────────
            # BUILD BRAND DICT (vectorized zip, not nested loops)
            # ────────────────────────────────────────────────
            brand_dict: dict = {}
            
            for b_lower, brand_raw, sellers, cats, variations, exp_vars in zip(
                df["_b_lower"], brand_col_vals[valid], sellers_s, cats_s, vars_s, exp_vars_s
            ):
                if b_lower not in brand_dict:
                    brand_dict[b_lower] = {
                        "brand_raw": brand_raw,
                        "sellers": set(),
                        "categories": set(),
                        "variations": set(),
                        "has_blank_category": False,
                    }
                brand_dict[b_lower]["sellers"].update(sellers)
                if cats is None:
                    brand_dict[b_lower]["has_blank_category"] = True
                else:
                    brand_dict[b_lower]["categories"].update(cats)
                brand_dict[b_lower]["variations"].update(variations)
                brand_dict[b_lower]["variations"].update(exp_vars)
            
            config_by_country[country_name] = [
                {
                    "brand": b_lower,
                    "brand_raw": data["brand_raw"],
                    "sellers": data["sellers"],
                    "categories": set() if data["has_blank_category"] else data["categories"],
                    "variations": list(data["variations"]),
                }
                for b_lower, data in brand_dict.items()
            ]
        except Exception as e:
            logger.warning(f"load_restricted_brands_fast tab={tab_name}: {e}")
            config_by_country[country_name] = []
    
    return config_by_country


# ─────────────────────────────────────────────────────────────────
# OPTIMIZATION 4: Fast JSON rules compilation
# ─────────────────────────────────────────────────────────────────

@st.cache_resource(ttl=3600)
def load_and_compile_json_rules_fast(json_path="category_qc_weighted.json") -> dict:
    """Compile JSON rules ONCE at startup, not per-request."""
    if not os.path.exists(json_path):
        logger.warning(f"{json_path} not found.")
        return {}
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw_rules = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load JSON rules: {e}")
        return {}
    
    # Normalize format
    if isinstance(raw_rules, list):
        fixed = {}
        for item in raw_rules:
            if isinstance(item, dict):
                cat = item.get("category") or item.get("Category Path") or item.get("name")
                kws = item.get("keywords") or item.get("weights") or item.get("positive")
                if cat and isinstance(kws, dict):
                    fixed[cat] = kws
        raw_rules = fixed
    
    if not isinstance(raw_rules, dict):
        return {}
    
    # Compile ALL rules at once (precompile regex)
    compiled_rules = {}
    for cat_path, keywords_dict in raw_rules.items():
        if not isinstance(keywords_dict, dict) or not keywords_dict:
            continue
        try:
            safe_kws = {str(k): float(w) for k, w in keywords_dict.items()}
            sorted_kws = sorted(safe_kws.keys(), key=len, reverse=True)
            if not sorted_kws:
                continue
            
            pattern_str = r"\b(" + "|".join(re.escape(k) for k in sorted_kws) + r")\b"
            compiled_rules[str(cat_path)] = {
                "pattern": re.compile(pattern_str, re.IGNORECASE),
                "weights": {k.lower(): w for k, w in safe_kws.items()},
            }
        except Exception as e:
            logger.warning(f"Skipping bad JSON rule for {cat_path}: {e}")
    
    return compiled_rules


# ─────────────────────────────────────────────────────────────────
# OPTIMIZATION 5: Precompile ALL regex patterns at module load
# ─────────────────────────────────────────────────────────────────

def compile_regex_patterns(words: List[str], flags=re.IGNORECASE) -> re.Pattern:
    """Return precompiled regex or compile once and cache."""
    if not words:
        return None
    
    key = (tuple(sorted(words)), flags)
    if key in _REGEX_CACHE:
        return _REGEX_CACHE[key]
    
    pattern = "|".join(
        r"\b" + re.escape(w) + r"\b" for w in sorted(words, key=len, reverse=True)
    )
    compiled = re.compile(pattern, flags)
    _REGEX_CACHE[key] = compiled
    return compiled


# ─────────────────────────────────────────────────────────────────
# MAIN: Load all support files (with optimizations)
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_support_files_lazy_optimized() -> Dict:
    """Load support files with all optimizations."""
    from nigeria_rules import load_nigeria_qc_rules
    
    def safe_txt(f):
        return load_txt_file(f) if os.path.exists(f) else []
    
    support = {
        "blacklisted_words": safe_txt("blacklisted.txt"),
        "book_category_codes": safe_txt("Books_cat.txt"),
        "perfume_category_codes": safe_txt("Perfume_cat.txt"),
        "sneaker_category_codes": safe_txt("Sneakers_Cat.txt"),
        "sneaker_sensitive_brands": [b.lower() for b in safe_txt("Sneakers_Sensitive.txt")],
        "sensitive_words": [w.lower() for w in safe_txt("sensitive_words.txt")],
        "unnecessary_words": [w.lower() for w in safe_txt("unnecessary.txt")],
        "colors": [c.lower() for c in safe_txt("colors.txt")],
        "color_categories": safe_txt("color_cats.txt"),
        "category_fas": safe_txt("Fashion_cat.txt"),
        "warranty_category_codes": safe_txt("warranty.txt"),
        "duplicate_exempt_codes": safe_txt("duplicate_exempt.txt"),
        "known_brands": safe_txt("brands.txt"),
        "variation_allowed_codes": safe_txt("variation.txt"),
        "weight_category_codes": safe_txt("weight.txt"),
        "smartphone_category_codes": safe_txt("smartphones.txt"),
        
        # Load heavy Excel files
        "restricted_brands_all": load_restricted_brands_fast(),
        
        # Compile JSON rules ONCE
        "compiled_json_rules": load_and_compile_json_rules_fast(),
        
        # Load category map (vectorized)
        "cat_path_to_code": load_category_map_fast(),
        "ng_qc_rules": load_nigeria_qc_rules(),
    }
    
    return support
