import re
import pandas as pd
import requests
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
import itertools
import threading

# ── API key pool — requests are round-robined across all keys ─────────────────
_API_KEYS = [
    "jvk_pQ7d0kw8FKwDnlwUzPaV7-IGo6IbT0dAp-vea4hPK2ckB4jPJnHIGctBrwUfIkt5",
    "jvk_WY4AOoCeG6BnanyCxBR1EYYeJVZyfYukBKwU3lDaNzTmtusSAi0RUneDpxN4YgLS",
    "jvk_ZL4c-ikTJn05XG-qGg6qV5MA2ubvgTSyORR5tYa6dNulZr6HfaEGuY7Yxk8JadED",
    "jvk_df0hse4uAHNeL-sJA5PEshtpvuv8zqFtKeS79TUidptIpgPJmRCIe5dUeZjusUFS",
]
_key_cycle = itertools.cycle(_API_KEYS)
_key_lock  = threading.Lock()

def _next_key() -> str:
    """Thread-safe round-robin key selector."""
    with _key_lock:
        return next(_key_cycle)

# Keep the old name as an alias so any existing call-sites still work
GATEWAY_API_KEY = _API_KEYS[0]

_AI_CACHE_KEY = "_audit_ai_cache"   # session-state key: {(sid, flag) -> True/False}
_MAX_WORKERS  = 20                  # more threads since we have 4 keys to spread load



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
    """
    Build and cache a single compiled regex from colors.txt.
    Matches any color as a whole word (case-insensitive).
    """
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
    try:
        xl = pd.ExcelFile("QC Check Validaton  (2).xlsx")
        sheet_name = f"Mandatory Attributes - {country_code.upper()}"
        if sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name)
            if "ID" in df.columns:
                df["ID"] = df["ID"].astype(str).str.strip()
                df = df[df["ID"].notna() & (df["ID"] != "") & (df["ID"] != "nan")]
                return df.set_index("ID").to_dict(orient="index")
    except Exception:
        pass
    return {}


# ── Session-state AI response cache ──────────────────────────────────────────
def _ai_cache() -> dict:
    """Get (or create) the per-session AI answer cache."""
    if _AI_CACHE_KEY not in st.session_state:
        st.session_state[_AI_CACHE_KEY] = {}
    return st.session_state[_AI_CACHE_KEY]


# ── Raw single-item AI callers (used by thread pool) ─────────────────────────
def _call_ai(prompt: str, timeout: int = 8) -> Optional[str]:
    """POST to the AI gateway using the next key in the rotation pool."""
    key = _next_key()   # thread-safe round-robin
    try:
        resp = requests.post(
            "https://ai-gateway.zuma.jumia.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            json={
                "model": "claude-sonnet-4.5",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            },
            timeout=timeout,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"].strip().upper()
    except Exception:
        pass
    return None



def check_ai_category(name: str, current_cat: str, suggested_cat: str) -> bool:
    """Returns True if AI thinks the original category is acceptable (wrongly rejected)."""
    if not name or not current_cat:
        return False
    prompt = (
        f"Product Name: {name}\n"
        f"Current Category: {current_cat}\n"
        f"AI Suggested Category: {suggested_cat}\n"
        f"The system rejected this item because it thought the current category is wrong. "
        f"Is the current category actually acceptable for this product? Reply YES or NO only."
    )
    ans = _call_ai(prompt)
    return "YES" in ans if ans else False


def check_ai_prohibited(name: str, current_cat: str) -> bool:
    """Returns True if AI thinks the item is harmless and was WRONGLY flagged as prohibited."""
    if not name:
        return False
    prompt = (
        f"Product Name: {name}\n"
        f"Current Category: {current_cat}\n"
        f"The AI rejected this as a 'Prohibited product' (e.g. adult toys, weapons, illegal items).\n"
        f"Is this actually a harmless product that was mistaken due to its name or category "
        f"(e.g., 'concrete vibrator' mistaken for a sex toy)? Reply YES if harmless, NO if truly prohibited."
    )
    ans = _call_ai(prompt)
    return "YES" in ans if ans else False

def get_false_approvals(approved_df: pd.DataFrame, data: pd.DataFrame, country_code: str) -> pd.DataFrame:
    """
    Scans approved items for missing mandatory fields based on the QC rules Excel.
    Returns a DataFrame of False Approvals (items the system let through but shouldn't have).
    Ignores manually-handled items (Is_Manual=True).

    Optimisations:
      - Pre-builds a SID→row dict so each lookup is O(1) instead of a full DataFrame scan.
      - Uses a pre-compiled regex for color matching (~100x faster than a 455-item any() loop).
    """
    color_re  = get_color_regex()          # single compiled regex from colors.txt
    qc_rules  = load_qc_excel(country_code)

    # ── Pre-build O(1) lookup: SID → row dict ─────────────────────────────────
    # Only materialise the columns actually used in the checks below
    _NEEDED = ['PRODUCT_SET_SID', 'CATEGORY_CODE', 'NAME', 'COLOR',
               'PRODUCT_WARRANTY', 'COUNT_VARIATIONS', 'LIST_VARIATIONS',
               'FDA', 'CATEGORY']
    data_lookup: dict = {}
    if not data.empty and "PRODUCT_SET_SID" in data.columns:
        _sub = data[[c for c in _NEEDED if c in data.columns]]
        data_lookup = {
            str(sid).strip(): row
            for sid, row in zip(_sub["PRODUCT_SET_SID"], _sub.to_dict("records"))
        }

    false_approvals = []

    for _, row in approved_df.iterrows():
        # Skip manually-handled items
        if str(row.get("Is_Manual", "")).strip().lower() == "true":
            continue

        sid    = str(row["ProductSetSid"]).strip()
        merged = data_lookup.get(sid, {})

        cat_code = str(merged.get("CATEGORY_CODE", "")).strip()
        name     = str(merged.get("NAME", "")).strip()
        rule     = qc_rules.get(cat_code, {})

        # ── FDA ────────────────────────────────────────────────────────────────
        if str(rule.get("FDA Documents", "")).strip().lower() == "mandatory":
            fda_val = str(merged.get("FDA", "")).strip()
            if not fda_val or fda_val.lower() in ("nan", "none", ""):
                false_approvals.append({
                    "ProductSetSid": sid,
                    "Status":  "Rejected",
                    "FLAG":    "[False Approval] Missed FDA",
                    "Comment": "AI approved but FDA is mandatory and missing.",
                })
                continue

        # ── Color ──────────────────────────────────────────────────────────────
        if str(rule.get("Color", "")).strip().lower() == "mandatory":
            color_col_val = str(merged.get("COLOR", "")).strip()
            color_in_col  = bool(color_col_val and color_col_val.lower() not in ("nan", "none", ""))
            # Single regex search instead of 455-iteration loop
            color_in_name = bool(color_re and color_re.search(name)) if name else False

            if not color_in_name and not color_in_col:
                false_approvals.append({
                    "ProductSetSid": sid,
                    "Status":  "Rejected",
                    "FLAG":    "[False Approval] Missed Color",
                    "Comment": "AI approved but Color is mandatory and missing from both title and color column.",
                })
                continue

        # ── Warranty ───────────────────────────────────────────────────────────
        if str(rule.get("Warranty", "")).strip().lower() == "mandatory":
            war_val = str(
                merged.get("PRODUCT_WARRANTY") or merged.get("product_warranty") or ""
            ).strip()
            if not war_val or war_val.lower() in ("nan", "none", ""):
                false_approvals.append({
                    "ProductSetSid": sid,
                    "Status":  "Rejected",
                    "FLAG":    "[False Approval] Missed Warranty",
                    "Comment": "AI approved but Warranty is mandatory and missing.",
                })
                continue

        # ── Variation ──────────────────────────────────────────────────────────
        if str(rule.get("Variation", "")).strip().lower() == "mandatory":
            var_count = str(
                merged.get("COUNT_VARIATIONS") or merged.get("count_variations") or ""
            ).strip()
            var_list  = str(
                merged.get("LIST_VARIATIONS") or merged.get("list_variations") or ""
            ).strip()
            var_present = (
                bool(var_count and var_count not in ("0", "nan", "none", ""))
                or bool(var_list and var_list.lower() not in ("nan", "none", "[]", ""))
            )
            if not var_present:
                false_approvals.append({
                    "ProductSetSid": sid,
                    "Status":  "Rejected",
                    "FLAG":    "[False Approval] Missed Variation",
                    "Comment": "AI approved but Variation is mandatory and missing.",
                })
                continue

    if false_approvals:
        return pd.DataFrame(false_approvals)
    return pd.DataFrame()


def get_true_rejection_sids(
    page_slice: pd.DataFrame,
    data: pd.DataFrame,
    country_code: str,
    cat_path_to_code: dict = None,
) -> set:
    """
    Evaluates a slice of rejected items and returns the set of SIDs that are
    TRUE rejections (correctly rejected by the AI).

    Speed improvements vs the original:
    ─────────────────────────────────────────────────────────────────────────
    1. Rule-based pre-screening  — color / warranty / variation / weight checks
       are resolved with pure pandas/dict logic (zero HTTP requests).
    2. Session-state AI cache    — per (SID, flag_type) result is stored so
       switching flags or reopening the modal never re-calls the API.
    3. ThreadPoolExecutor        — all remaining items that need an AI call are
       submitted simultaneously and results are collected as they complete.
    ─────────────────────────────────────────────────────────────────────────
    """
    true_rejections: set  = set()
    ai_cache: dict        = _ai_cache()   # {(sid, flag_kind) -> bool}

    weights  = load_weight_categories()
    color_re = get_color_regex()
    qc_rules = load_qc_excel(country_code)

    # ── O(1) SID → raw-data-row lookup ───────────────────────────────────────
    # Only materialise columns needed for the checks below
    _NEEDED = ['PRODUCT_SET_SID', 'CATEGORY_CODE', 'NAME', 'COLOR',
               'PRODUCT_WARRANTY', 'COUNT_VARIATIONS', 'LIST_VARIATIONS',
               'FDA', 'CATEGORY']
    data_lookup: dict = {}
    if not data.empty and "PRODUCT_SET_SID" in data.columns:
        _sub = data[[c for c in _NEEDED if c in data.columns]]
        data_lookup = {
            str(sid).strip(): row
            for sid, row in zip(_sub["PRODUCT_SET_SID"], _sub.to_dict("records"))
        }

    total = len(page_slice)
    st.session_state["_bg_audit_progress"]      = 0.0 if total > 0 else 1.0
    st.session_state["_bg_audit_progress_text"] = (
        f"Processed 0/{total} items" if total > 0 else "No items to audit."
    )

    # ── Phase 1: rule-based pre-screening (no AI, instant) ───────────────────
    # Collect items that still need an AI call after pre-screening.
    # ai_tasks: list of (sid, flag_kind, name, current_cat, sug_cat)
    ai_tasks: list = []

    # Use to_dict('records') — avoids per-row Series allocation from iterrows()
    for row in page_slice.to_dict("records"):
        sid  = str(row.get("ProductSetSid", "")).strip()
        flag = str(row.get("FLAG", "")).lower()

        merged      = data_lookup.get(sid, {})
        cat_code    = str(merged.get("CATEGORY_CODE", "")).strip()
        name        = str(merged.get("NAME", "")).strip()
        current_cat = str(merged.get("CATEGORY", "")).strip()
        rule        = qc_rules.get(cat_code, {})
        sug_cat     = str(row.get("Suggested_Categories", ""))

        # ── Color (pure rule — no AI needed) ─────────────────────────────────
        if "color" in flag:
            col_req       = str(rule.get("Color", "Mandatory")).strip().lower()
            color_col_val = str(merged.get("COLOR", "")).strip()
            color_in_col  = bool(color_col_val and color_col_val.lower() not in ("nan", "none", ""))
            color_in_name = bool(color_re and color_re.search(name)) if name else False
            if not color_in_name and not color_in_col and col_req != "no need":
                true_rejections.add(sid)   # color genuinely absent
            # else: wrongly rejected → stays in audit
            continue

        # ── Warranty (pure rule — no AI needed) ───────────────────────────────
        if "warranty" in flag:
            war_req = str(rule.get("Warranty", "Mandatory")).strip().lower()
            war_val = str(
                merged.get("PRODUCT_WARRANTY") or merged.get("product_warranty") or ""
            ).strip()
            war_present = bool(war_val and war_val.lower() not in ("nan", "none", ""))
            if not war_present and war_req != "no need":
                true_rejections.add(sid)
            continue

        # ── Weight / Title language (pure rule — no AI needed) ────────────────
        if "title language" in flag or "weight" in flag:
            if cat_code in weights:
                true_rejections.add(sid)
            continue

        # ── Variation — can often be resolved without AI ──────────────────────
        if "variation" in flag:
            var_req   = str(rule.get("Variation", "Mandatory")).strip().lower()
            var_count = str(merged.get("COUNT_VARIATIONS") or merged.get("count_variations") or "").strip()
            var_list  = str(merged.get("LIST_VARIATIONS")  or merged.get("list_variations")  or "").strip()
            var_present = (
                bool(var_count and var_count not in ("0", "nan", "none", ""))
                or bool(var_list and var_list.lower() not in ("nan", "none", "[]", ""))
            )
            if var_present:
                continue   # seller has variations → wrongly rejected
            if var_req == "no need":
                continue   # category doesn't need variation → wrongly rejected
            # Ambiguous — need AI to check if category is even correct
            ai_tasks.append((sid, "variation", name, current_cat, sug_cat, cat_code, rule))
            continue

        # ── FDA — can be partially resolved without AI ────────────────────────
        if "fda" in flag:
            fda_req     = str(rule.get("FDA Documents", "Mandatory")).strip().lower()
            fda_val     = str(merged.get("FDA", "")).strip()
            fda_present = bool(fda_val and fda_val.lower() not in ("nan", "none", ""))
            if fda_present:
                continue   # FDA is present → wrongly rejected
            # Need AI to determine if category is correct / FDA actually required
            ai_tasks.append((sid, "fda", name, current_cat, sug_cat, cat_code, rule))
            continue

        # ── Wrong Category ────────────────────────────────────────────────────
        if "category" in flag and "prohibited" not in flag:
            ai_tasks.append((sid, "category", name, current_cat, sug_cat, cat_code, rule))
            continue

        # ── Prohibited ────────────────────────────────────────────────────────
        if "prohibited" in flag:
            ai_tasks.append((sid, "prohibited", name, current_cat, sug_cat, cat_code, rule))
            continue

    # ── Phase 2: parallel AI calls for remaining ambiguous items ─────────────
    if ai_tasks:
        st.session_state["_bg_audit_progress_text"] = (
            f"🤖 Running {len(ai_tasks)} AI checks in parallel..."
        )

        def _resolve_one(task):
            """Run one AI decision.  Returns (sid, is_true_rejection)."""
            sid, kind, name, current_cat, sug_cat, cat_code, rule = task

            # ── Check session cache first ──────────────────────────────────
            cache_key = (sid, kind)
            if cache_key in ai_cache:
                return (sid, ai_cache[cache_key])

            result = False   # default: NOT a true rejection (shows in audit)

            if kind == "category":
                is_acceptable = check_ai_category(name, current_cat, sug_cat)
                result = not is_acceptable  # true rejection if category IS wrong

            elif kind == "prohibited":
                is_harmless = check_ai_prohibited(name, current_cat)
                result = not is_harmless    # true rejection if NOT harmless

            elif kind == "fda":
                fda_req           = str(rule.get("FDA Documents", "Mandatory")).strip().lower()
                category_ok       = check_ai_category(name, current_cat, sug_cat)
                if category_ok:
                    result = (fda_req != "no need")
                else:
                    sug_code = (cat_path_to_code or {}).get(sug_cat.strip().lower(), "")
                    sug_rule = qc_rules.get(sug_code, {})
                    sug_fda  = str(sug_rule.get("FDA Documents", "Mandatory")).strip().lower() if sug_rule else "mandatory"
                    result   = (sug_fda != "no need")

            elif kind == "variation":
                var_req     = str(rule.get("Variation", "Mandatory")).strip().lower()
                category_ok = check_ai_category(name, current_cat, sug_cat)
                if category_ok:
                    result = (var_req != "no need")
                else:
                    sug_code = (cat_path_to_code or {}).get(sug_cat.strip().lower(), "")
                    sug_rule = qc_rules.get(sug_code, {})
                    sug_var  = str(sug_rule.get("Variation", "Mandatory")).strip().lower() if sug_rule else "mandatory"
                    result   = (sug_var != "no need")

            ai_cache[cache_key] = result
            return (sid, result)

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(_resolve_one, t): t for t in ai_tasks}
            done    = 0
            for future in as_completed(futures):
                try:
                    sid, is_true = future.result()
                    if is_true:
                        true_rejections.add(sid)
                except Exception:
                    pass
                done += 1
                pct = done / len(ai_tasks)
                st.session_state["_bg_audit_progress"]      = pct
                st.session_state["_bg_audit_progress_text"] = (
                    f"🤖 AI checks: {done}/{len(ai_tasks)} complete..."
                )

    st.session_state["_bg_audit_progress"]      = 1.0
    st.session_state["_bg_audit_progress_text"] = "✅ Validation complete."
    return true_rejections
