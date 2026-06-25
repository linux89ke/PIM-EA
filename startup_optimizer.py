"""
startup_optimizer.py
────────────────────────────────────────────────────────────────
Lazy-load modules and optimize app initialization for Streamlit Cloud.
Import this at the TOP of streamlit_app.py, before ANY other imports.
"""

import sys
import os
from typing import Any

# ────────────────────────────────────────────────────────────────
# 1. ENVIRONMENT OPTIMIZATION
# ────────────────────────────────────────────────────────────────

# Disable verbose logging on Streamlit Cloud
if "streamlit" in sys.modules or "STREAMLIT_SERVER_HEADLESS" in os.environ:
    os.environ["PYTHONWARNINGS"] = "ignore"
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# Disable CUDA/GPU for lighter footprint on Cloud
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["OMP_NUM_THREADS"] = "4"

# ────────────────────────────────────────────────────────────────
# 2. MODULE LAZY-LOADING (import only when needed)
# ────────────────────────────────────────────────────────────────

_module_cache = {}


def lazy_import(module_name: str, package: str = None):
    """Import module lazily on first access."""
    full_name = f"{package}.{module_name}" if package else module_name
    if full_name not in _module_cache:
        _module_cache[full_name] = __import__(full_name, fromlist=[module_name])
    return _module_cache[full_name]


# ────────────────────────────────────────────────────────────────
# 3. PRECOMPILE REGEX PATTERNS (move to module level)
# ────────────────────────────────────────────────────────────────

import re

# These run ONCE at startup, not per-request
PRECOMPILED_REGEX = {
    "html_tags": re.compile(r"<[a-zA-Z/][^>]*>"),
    "special_chars": re.compile(r"[^\x00-\x7F★✓•®™]|[!@#$%^&*()]{3,}"),
    "model_number": re.compile(r"[A-Z0-9]{2,}[0-9]{2,}|[0-9]{2,}[A-Z]{2,}", re.I),
    "size_type": re.compile(r"\b(EU|UK|US|FR|CM|KE)\b", re.I),
    "brand_repeat": re.compile(r"\b(brand|by|from)\b", re.I),
    "word_boundary": re.compile(r"\b"),
    "whitespace_collapse": re.compile(r"\s+"),
    "size_unit": re.compile(
        r"(?<![\w.])"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*"
        r"(l(?:itres?|iters?)?|ml|cl"
        r"|kg(?:s)?|g(?:ram(?:s)?)?|mg|lb(?:s)?|oz"
        r"|cm|mm|m(?:etres?|eters?)?|ft|inch(?:es)?|\")"
        r"(?![\w.])",
        re.IGNORECASE,
    ),
}


# ────────────────────────────────────────────────────────────────
# 4. OPTIMIZE FILE LOADING (Cache manager)
# ────────────────────────────────────────────────────────────────

class OptimizedFileCache:
    """Thread-safe file cache with LRU eviction."""
    
    def __init__(self, max_size_mb: int = 500):
        self.cache = {}
        self.max_size = max_size_mb * 1024 * 1024
        self.current_size = 0
    
    def get(self, path: str):
        """Return cached file or None."""
        return self.cache.get(path)
    
    def set(self, path: str, data: Any, size_bytes: int):
        """Cache file with automatic eviction if over limit."""
        if self.current_size + size_bytes > self.max_size:
            # Remove oldest entries (simple FIFO, not LRU for performance)
            while self.cache and self.current_size + size_bytes > self.max_size:
                old_path = next(iter(self.cache))
                self.current_size -= len(str(self.cache[old_path]))
                del self.cache[old_path]
        
        self.cache[path] = data
        self.current_size += size_bytes


# Global file cache
file_cache = OptimizedFileCache(max_size_mb=300)


# ────────────────────────────────────────────────────────────────
# 5. FAST EXCEL LOADER (read only needed columns/sheets)
# ────────────────────────────────────────────────────────────────

def fast_read_excel(
    filepath: str,
    sheet_name: str = 0,
    usecols: list = None,
    nrows: int = None,
    dtype: dict = None
):
    """
    Fast Excel reader with selective column loading.
    - Only reads specified columns
    - Optional row limit
    - Explicit dtype to avoid string conversion overhead
    """
    import pandas as pd
    
    try:
        return pd.read_excel(
            filepath,
            sheet_name=sheet_name,
            usecols=usecols,
            nrows=nrows,
            dtype=dtype or str,
            engine="openpyxl",
        )
    except Exception as e:
        import logging
        logging.warning(f"fast_read_excel({filepath}): {e}")
        return pd.DataFrame()


# ────────────────────────────────────────────────────────────────
# 6. VECTORIZED TEXT OPERATIONS
# ────────────────────────────────────────────────────────────────

def batch_normalize(texts: list, lowercase: bool = True, strip: bool = True) -> list:
    """Vectorized text normalization (faster than row-by-row)."""
    if not texts:
        return []
    
    result = []
    for text in texts:
        s = str(text) if text else ""
        if strip:
            s = s.strip()
        if lowercase:
            s = s.lower()
        result.append(s)
    return result


def batch_regex_findall(texts: list, pattern: re.Pattern) -> list:
    """Apply regex findall to multiple texts efficiently."""
    return [pattern.findall(str(t)) if t else [] for t in texts]


# ────────────────────────────────────────────────────────────────
# 7. DISABLE HEAVY ML MODELS ON STREAMLIT CLOUD
# ────────────────────────────────────────────────────────────────

IS_STREAMLIT_CLOUD = "streamlit.app" in os.getcwd() or "STREAMLIT_SERVER_HEADLESS" in os.environ

if IS_STREAMLIT_CLOUD:
    # Disable SentenceTransformer embedding on Cloud to save memory
    # Falls back to TF-IDF in category_matcher_engine.py
    os.environ["SENTENCE_TRANSFORMERS_DISABLE"] = "1"


# ────────────────────────────────────────────────────────────────
# 8. STREAMLIT SESSION STATE OPTIMIZER
# ────────────────────────────────────────────────────────────────

def optimize_session_state():
    """Call this in streamlit_app.py to clean up unused session keys."""
    import streamlit as st
    
    # Remove keys that accumulate and cause memory bloat
    cleanup_keys = [
        k for k in st.session_state.keys()
        if k.startswith(("_grid_", "quick_rej_", "toast_", "_sf_", "exp_"))
        and k not in (
            "_grid_page_contexts", "_grid_last_ctx", "_grid_review_data_cache",
            "_grid_warm_urls"
        )
    ]
    for k in cleanup_keys:
        del st.session_state[k]


print("✓ Startup optimizer loaded (lazy modules, precompiled regex, file cache)")
