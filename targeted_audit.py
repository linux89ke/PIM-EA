import streamlit as st
import pandas as pd
from targeted_audit_filters import (
    get_false_approvals,
    evaluate_rejections,
    get_image_extraction_errors,
    CHECK_ORDER,
    CHECK_LABELS,
)


@st.dialog("Targeted Audit", width="large", dismissible=False)
def targeted_audit_modal(support_files):
    st.markdown("### :material/fact_check: Targeted Audit")
    st.write(
        "Runs deterministic, rule-based validation on every check the pipeline performs — "
        "category, color, warranty, variation, FDA, title language/weight, name↔brand, "
        "image quality, brand-on-image — plus infrastructure failures (image extraction). "
        "No AI/network calls involved, so results are instant and repeatable."
    )

    fr = st.session_state.get("final_report", pd.DataFrame())
    data = st.session_state.get("all_data_map", pd.DataFrame())
    country = st.session_state.get("selected_country", "Egypt")
    country_code = {"Egypt": "EG", "Senegal": "SN", "Ivory Coast": "CI"}.get(country, "EG")

    if fr.empty or data.empty:
        st.warning("No data available to audit. Please upload and process files first.")
        if st.button("Close", key="btn_close_audit_empty"):
            st.session_state.show_targeted_audit_modal = False
            st.rerun()
        return

    run_col, clear_col = st.columns([3, 1])
    with run_col:
        run_clicked = st.button("🔍 Run Full Targeted Audit", width='stretch', type="primary")
    with clear_col:
        if st.button("Clear Results", width='stretch'):
            for k in ("_audit_false_approvals", "_audit_rejection_eval", "_audit_image_errors"):
                st.session_state.pop(k, None)
            st.rerun()

    if run_clicked:
        approved_df = fr[fr["Status"] == "Approved"] if "Status" in fr.columns else pd.DataFrame()
        rejected_df = fr[fr["Status"] == "Rejected"] if "Status" in fr.columns else pd.DataFrame()

        with st.spinner("Auditing approved items for missed violations..."):
            st.session_state["_audit_false_approvals"] = (
                get_false_approvals(approved_df, data, country_code) if not approved_df.empty else pd.DataFrame()
            )

        with st.spinner("Validating rejected items check-by-check..."):
            st.session_state["_audit_rejection_eval"] = (
                evaluate_rejections(rejected_df, data, country_code) if not rejected_df.empty else pd.DataFrame()
            )

        st.session_state["_audit_image_errors"] = get_image_extraction_errors(data)

    false_approvals = st.session_state.get("_audit_false_approvals", pd.DataFrame())
    rejection_eval = st.session_state.get("_audit_rejection_eval", pd.DataFrame())
    image_errors = st.session_state.get("_audit_image_errors", pd.DataFrame())

    has_results = not false_approvals.empty or not rejection_eval.empty or not image_errors.empty

    if has_results:
        st.markdown("---")

        # ── Summary strip ──────────────────────────────────────────────────
        n_false_approvals = len(false_approvals)
        n_false_rejections = int((rejection_eval["Classification"] == "False Rejection").sum()) if not rejection_eval.empty else 0
        n_true_rejections = int((rejection_eval["Classification"] == "True Rejection").sum()) if not rejection_eval.empty else 0
        n_manual = int((rejection_eval["Classification"] == "Needs Manual Review").sum()) if not rejection_eval.empty else 0

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("False Approvals", n_false_approvals)
        s2.metric("False Rejections", n_false_rejections)
        s3.metric("Confirmed True Rejections", n_true_rejections)
        s4.metric("Needs Manual Review", n_manual)
        s5.metric("Image Extraction Errors", len(image_errors))

        st.markdown("#### Results by check")

        # ── One expandable per check ─────────────────────────────────────────
        for check_key in CHECK_ORDER:
            label = CHECK_LABELS[check_key]

            fa_slice = false_approvals[false_approvals["Check"] == check_key] if not false_approvals.empty else pd.DataFrame()
            rej_slice = rejection_eval[rejection_eval["Check"] == check_key] if not rejection_eval.empty else pd.DataFrame()

            n_issues = len(fa_slice) + len(rej_slice)
            if n_issues == 0:
                continue  # keep the modal clean — only show checks with findings

            with st.expander(f"**{label}** — {n_issues} item(s) flagged", expanded=False):
                if not fa_slice.empty:
                    st.markdown("**❌ False Approvals** — approved but should have been rejected")
                    st.dataframe(fa_slice[["ProductSetSid", "Detail"]], width='stretch', hide_index=True)

                if not rej_slice.empty:
                    for classification, emoji in [
                        ("False Rejection", "⚠️"),
                        ("True Rejection", "✅"),
                        ("Needs Manual Review", "👁️"),
                    ]:
                        sub = rej_slice[rej_slice["Classification"] == classification]
                        if sub.empty:
                            continue
                        st.markdown(f"**{emoji} {classification}** ({len(sub)})")
                        st.dataframe(sub[["ProductSetSid", "Reason"]], width='stretch', hide_index=True)

        # ── Infrastructure failures: image extraction ────────────────────────
        if not image_errors.empty:
            with st.expander(f"**🖼️ Image Extraction Errors** — {len(image_errors)} item(s)", expanded=False):
                st.caption("These are pipeline/infrastructure failures (broken connections, timeouts) — "
                           "not QC decisions. The product's image was never successfully processed.")
                st.dataframe(image_errors, width='stretch', hide_index=True)

    elif run_clicked:
        st.success("✅ No issues found across any check.")

    st.markdown("---")
    if st.button("Close", key="btn_close_audit_modal", type="secondary", width='stretch'):
        st.session_state.show_targeted_audit_modal = False
        st.rerun()