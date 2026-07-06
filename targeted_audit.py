import streamlit as st
import pandas as pd
from targeted_audit_filters import (
    evaluate_all_checks,
    diagnose_columns,
    CHECK_ORDER,
    CHECK_LABELS,
)
from report_builder import build_docx_report

_CHECK_ICONS = {
    "skip": "⏭️",
    "duplicate": "📑",
    "category": "🗂️",
    "color": "🎨",
    "warranty": "🛡️",
    "variation": "🔀",
    "fda": "💊",
    "title_language": "⚖️",
    "name_brand": "🏷️",
    "image_quality": "🖼️",
    "image_extraction": "🔌",
    "ai_caption": "🖋️",
    "brand_image": "🔎",
}

_VERDICT_STYLE = {
    "True Rejection":      ("#16a34a", "#dcfce7", "✅"),
    "False Rejection":     ("#d97706", "#fef3c7", "⚠️"),
    "False Approval":      ("#dc2626", "#fee2e2", "❌"),
    "Needs Manual Review": ("#2563eb", "#dbeafe", "👁️"),
    "AI Error":            ("#7c3aed", "#ede9fe", "🚨"),
    "Skipped":             ("#64748b", "#f1f5f9", "⏭️"),
    "Duplicate":           ("#0891b2", "#cffafe", "📑"),
}
_VERDICT_ORDER = ["False Approval", "False Rejection", "True Rejection",
                  "Needs Manual Review", "AI Error", "Duplicate", "Skipped"]


def _inject_css():
    st.markdown("""
    <style>
      div[data-testid="stDialog"] div[data-testid="stMetric"] {
          background: linear-gradient(180deg, rgba(255,255,255,0.6), rgba(248,250,252,0.9));
          border: 1px solid rgba(0,0,0,0.06);
          border-radius: 12px;
          padding: 14px 12px 10px 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }
      .audit-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 3px 12px;
          border-radius: 999px;
          font-size: 0.82rem;
          font-weight: 600;
          margin: 2px 6px 8px 0;
      }
      .audit-reason-label {
          font-size: 0.88rem;
          font-weight: 600;
          margin: 10px 0 4px 0;
          color: rgba(49,51,63,0.85);
      }
      .audit-section-title {
          font-size: 1.05rem;
          font-weight: 700;
          margin: 18px 0 6px 0;
          letter-spacing: 0.2px;
      }
      .audit-caption {
          color: rgba(49,51,63,0.6);
          font-size: 0.85rem;
          margin-bottom: 10px;
      }
      hr.audit-divider {
          margin: 14px 0;
          border: none;
          border-top: 1px solid rgba(0,0,0,0.08);
      }
    </style>
    """, unsafe_allow_html=True)


def _pill(text: str, fg: str, bg: str) -> str:
    return f'<span class="audit-pill" style="color:{fg}; background:{bg};">{text}</span>'


def _has_content(series: pd.Series) -> bool:
    return series.fillna("").astype(str).str.strip().replace({"nan": "", "None": ""}).ne("").any()


def _display_columns(df: pd.DataFrame) -> list:
    universal = ["ProductSetSid", "Product Name", "Category"]
    tail = [c for c in ("Detail",) if c in df.columns]
    reserved = universal + tail + ["Check", "Reason Type", "Verdict"]
    # Only keep check-specific columns that actually have data in *this*
    # slice — since all checks share one combined DataFrame, an unrelated
    # check's column (e.g. FDA on a Color-check row) would otherwise show
    # up entirely empty.
    middle = [c for c in df.columns if c not in reserved and _has_content(df[c])]
    return [c for c in universal if c in df.columns] + middle + tail


def _column_config(df: pd.DataFrame) -> dict:
    cfg = {}
    if "Image" in df.columns:
        try:
            cfg["Image"] = st.column_config.ImageColumn("Image", width="small")
        except Exception:
            cfg["Image"] = st.column_config.LinkColumn("Image")
    if "ProductSetSid" in df.columns:
        cfg["ProductSetSid"] = st.column_config.TextColumn("SID", width="small")
    return cfg


def _render_table(df: pd.DataFrame):
    cols = _display_columns(df)
    st.dataframe(df[cols], width='stretch', hide_index=True, column_config=_column_config(df))


@st.dialog("Targeted Audit", width="large", dismissible=True)
def targeted_audit_modal(support_files):
    _inject_css()

    st.markdown("### :material/fact_check: Targeted Audit")
    st.markdown(
        '<div class="audit-caption">Reads the file\'s own per-check status and reason columns '
        "directly. Every distinct reason is broken out on its own — nothing is merged into a "
        "generic bucket — and any reason mentioning an error is separated out as an AI Error "
        "rather than treated as a real finding.</div>",
        unsafe_allow_html=True,
    )

    fr = st.session_state.get("final_report", pd.DataFrame())
    data = st.session_state.get("all_data_map", pd.DataFrame())
    country = st.session_state.get("selected_country", "Egypt")
    country_code = {"Kenya": "KE", "Uganda": "UG", "Nigeria": "NG", "Ghana": "GH", "Morocco": "MA", "Egypt": "EG", "Senegal": "SN", "Ivory Coast": "CI"}.get(country, "EG")

    if data.empty:
        st.warning("No data available to audit. Please upload and process files first.")
        if st.button("Close", key="btn_close_audit_empty"):
            st.session_state.show_targeted_audit_modal = False
            st.rerun()
        return

    with st.expander("🔧 Column diagnostics (click if a check seems to be missing)", expanded=False):
        st.caption(
            "Each check reads two specific columns from your data. If a check's results never "
            "show up — even though you'd expect findings — it usually means the column name "
            "below doesn't match your file exactly. ❌ means that exact column name wasn't found."
        )
        diag = diagnose_columns(data)
        st.dataframe(diag, width='stretch', hide_index=True)

    run_col, search_col, clear_col = st.columns([1.6, 2, 1])
    with run_col:
        run_clicked = st.button("🔍 Run Full Audit", width='stretch', type="primary")
    with search_col:
        search_term = st.text_input(
            "Filter by product name or SID", value="", placeholder="Search results...",
            label_visibility="collapsed",
        )
    with clear_col:
        if st.button("Clear Results", width='stretch'):
            st.session_state.pop("_audit_results", None)
            st.rerun()

    if run_clicked:
        with st.spinner("Re-validating every check against the file's own rules..."):
            st.session_state["_audit_results"] = evaluate_all_checks(data, country_code)

        # Build the Word report once here, not on every rerun — with 1000+
        # rows this can take a few seconds, and rebuilding it on every
        # keystroke in the search box made it look broken/missing.
        st.session_state.pop("_audit_docx_bytes", None)
        st.session_state.pop("_audit_docx_error", None)
        with st.spinner("Building Word report... this can take a moment for large files."):
            try:
                st.session_state["_audit_docx_bytes"] = build_docx_report(
                    fr, st.session_state["_audit_results"], country_label=country_code,
                )
            except Exception as e:
                st.session_state["_audit_docx_error"] = f"{type(e).__name__}: {e}"

    results = st.session_state.get("_audit_results", pd.DataFrame())

    def _filter(df):
        if df.empty or not search_term.strip():
            return df
        term = search_term.strip().lower()
        mask = pd.Series(False, index=df.index)
        for col in ("ProductSetSid", "Product Name", "NAME"):
            if col in df.columns:
                mask = mask | df[col].astype(str).str.lower().str.contains(term, na=False)
        return df[mask]

    results_f = _filter(results)

    # Only these verdicts represent something that actually needs attention.
    # "True Rejection" (system was right) and "Skipped" (correctly excluded
    # before QC) are the pipeline working as intended, not issues — they're
    # still counted in the metrics strip for context, just not listed below.
    _ISSUE_VERDICTS = {"False Approval", "False Rejection", "Needs Manual Review", "AI Error", "Duplicate"}
    results_issues = results_f[results_f["Verdict"].isin(_ISSUE_VERDICTS)] if not results_f.empty else results_f

    has_results = not results.empty

    if has_results:
        st.markdown('<hr class="audit-divider">', unsafe_allow_html=True)

        counts = results["Verdict"].value_counts() if not results.empty else pd.Series(dtype=int)
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        s1.metric("❌ False Approvals", int(counts.get("False Approval", 0)))
        s2.metric("⚠️ False Rejections", int(counts.get("False Rejection", 0)))
        s3.metric("✅ True Rejections", int(counts.get("True Rejection", 0)))
        s4.metric("👁️ Needs Review", int(counts.get("Needs Manual Review", 0)))
        s5.metric("🚨 AI Errors", int(counts.get("AI Error", 0)))
        s6.metric("📑 Duplicates", int(counts.get("Duplicate", 0)))

        st.markdown('<div class="audit-section-title">Issues by check</div>', unsafe_allow_html=True)
        st.caption("Correctly confirmed rejections and normal pre-QC exclusions are counted above "
                   "but not listed here — only items that need a decision or a fix are shown.")

        any_visible = False
        for check_key in CHECK_ORDER:
            label = CHECK_LABELS[check_key]
            icon = _CHECK_ICONS.get(check_key, "🔹")

            check_slice = results_issues[results_issues["Check"] == check_key] if not results_issues.empty else pd.DataFrame()
            if check_slice.empty:
                continue
            any_visible = True

            with st.expander(f"{icon}  **{label}**   —   {len(check_slice)} item(s) flagged", expanded=False):
                for reason_type in check_slice["Reason Type"].unique():
                    reason_slice = check_slice[check_slice["Reason Type"] == reason_type]
                    st.markdown(f'<div class="audit-reason-label">{reason_type} '
                                f'({len(reason_slice)})</div>', unsafe_allow_html=True)

                    for verdict in _VERDICT_ORDER:
                        sub = reason_slice[reason_slice["Verdict"] == verdict]
                        if sub.empty:
                            continue
                        fg, bg, emoji = _VERDICT_STYLE[verdict]
                        st.markdown(_pill(f"{emoji} {verdict} ({len(sub)})", fg, bg), unsafe_allow_html=True)
                        _render_table(sub)

        if not any_visible and search_term.strip():
            st.info(f"No results match '{search_term}'.")

        # ── Export ───────────────────────────────────────────────────────────
        dl_col1, dl_col2 = st.columns(2)
        with dl_col1:
            if not results.empty:
                st.download_button(
                    "⬇️ Download CSV",
                    data=results.to_csv(index=False).encode("utf-8"),
                    file_name="targeted_audit_results.csv",
                    mime="text/csv",
                    width='stretch',
                )
        with dl_col2:
            docx_bytes = st.session_state.get("_audit_docx_bytes")
            docx_error = st.session_state.get("_audit_docx_error")
            if docx_bytes:
                st.download_button(
                    "📄 Download Report (Word)",
                    data=docx_bytes,
                    file_name=f"QC_{country_code}_Audit_Report_{pd.Timestamp.today():%d_%b_%Y}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    width='stretch',
                )
            elif docx_error:
                st.error(f"Couldn't build the Word report: {docx_error}")
            else:
                st.caption("Word report will be ready after you run the audit.")

    elif run_clicked:
        st.success("✅ No issues found across any check.")

    st.markdown('<hr class="audit-divider">', unsafe_allow_html=True)
    if st.button("Close", key="btn_close_audit_modal", type="secondary", width='stretch'):
        st.session_state.show_targeted_audit_modal = False
        st.rerun()