import streamlit as st
import pandas as pd
import time
from targeted_audit_filters import get_false_approvals, get_true_rejection_sids

@st.dialog("Targeted Audit", width="large")
def targeted_audit_modal(support_files):
    st.markdown("### :material/psychology: AI-Powered Targeted Audit")
    st.write("Run deep AI validation on current selections to identify false approvals and true rejections.")
    
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

    c1, c2 = st.columns(2)
    
    with c1:
        st.markdown("#### False Approvals")
        st.caption("Check items marked as 'Approved' that might actually violate rules.")
        if st.button("Find False Approvals", width='stretch', type="primary"):
            approved_df = fr[fr["Status"] == "Approved"] if "Status" in fr.columns else pd.DataFrame()
            if approved_df.empty:
                st.info("No approved items found to audit.")
            else:
                with st.spinner("Running AI Analysis..."):
                    false_approvals = get_false_approvals(approved_df, data, country_code)
                    if not false_approvals.empty:
                        st.session_state.targeted_audit_false_approvals = false_approvals
                        st.success(f"Found {len(false_approvals)} potential false approvals!")
                    else:
                        st.success("No false approvals detected!")

    with c2:
        st.markdown("#### True Rejections")
        st.caption("Validate items marked as 'Rejected' to confirm they are correctly flagged.")
        if st.button("Validate Rejections", width='stretch', type="primary"):
            rejected_df = fr[fr["Status"] == "Rejected"] if "Status" in fr.columns else pd.DataFrame()
            if rejected_df.empty:
                st.info("No rejected items found to audit.")
            else:
                with st.spinner("Running AI Analysis..."):
                    true_rejections = get_true_rejection_sids(rejected_df, data, country_code)
                    if true_rejections:
                        st.session_state.targeted_audit_true_rejections = true_rejections
                        st.success(f"Confirmed {len(true_rejections)} true rejections!")
                    else:
                        st.success("No true rejections confirmed.")

    # Show Progress if available
    progress = st.session_state.get("_bg_audit_progress", 0.0)
    prog_text = st.session_state.get("_bg_audit_progress_text", "")
    if progress > 0 and progress < 1.0:
        st.progress(progress, text=prog_text)

    # Show results
    if "targeted_audit_false_approvals" in st.session_state:
        st.markdown("---")
        st.subheader("Potential False Approvals")
        st.dataframe(st.session_state.targeted_audit_false_approvals, width='stretch', hide_index=True)
    
    st.markdown("---")
    if st.button("Close", key="btn_close_audit_modal", type="secondary", width='stretch'):
        st.session_state.show_targeted_audit_modal = False
        st.rerun()
