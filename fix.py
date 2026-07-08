import re
import json

path = 'c:/Users/Charles.Kireki/Desktop/pim2026-test - Copy - Copy (2) - work 1st 7/streamlit_app.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

good_handle_jtbridge = """@st.fragment
def handle_jtbridge():
    _bridge_val = st.text_input(
        "jtbridge",
        value="",
        placeholder="JTBRIDGE_UNIQUE_DO_NOT_USE",
        key=f"main_bridge_{st.session_state.main_bridge_counter}",
        label_visibility="collapsed",
    )

    if _bridge_val:
        try:
            _msg = json.loads(_bridge_val)
            if _msg.get("action") == "reject_comments":
                _ac = _msg.get("payload", {})
                if isinstance(_ac, dict):
                    if "pending_auto_comments" not in st.session_state: st.session_state.pending_auto_comments = {}
                    st.session_state.pending_auto_comments.update(_ac)
            elif _msg.get("action") == "reject":
                _payload = _msg.get("payload", {})
                _auto_comments = st.session_state.pop("pending_auto_comments", {})
                if isinstance(_payload, dict) and _payload:
                    _rgroups = {}
                    for _sid, _rkey in _payload.items(): _rgroups.setdefault(_rkey, []).append(_sid)
                    _total = 0
                    for _rkey, _sids in _rgroups.items():
                        if _rkey.startswith("Other Reason (Custom): "):
                            _flag = "Other Reason (Custom)"
                            _code = "1000007 - Other Reason"
                            _cmt = _rkey.split(": ", 1)[1]
                        else:
                            _IMAGE_FLAG_FALLBACK = {"REJECT_IMG_STRETCHED": "Image Stretched", "REJECT_IMG_BLURRY": "Image Blurry", "REJECT_IMG_MISMATCH": "Image Mismatch", "REJECT_IMG_INFRINGING": "Image Infringing", "REJECT_IMG_TOO_MANY": "Image Too Many things displayed"}
                            _flag = REASON_MAP.get(_rkey) or _IMAGE_FLAG_FALLBACK.get(_rkey, "Other Reason (Custom)")
                            _rinfo = support_files["flags_mapping"].get(_flag, {"reason": "1000007 - Other Reason", "en": "Manual rejection"})
                            _code = _rinfo["reason"]
                            _cmt_lang = "fr" if st.session_state.selected_country == "Morocco" else "en"
                            _cmt = _rinfo.get(_cmt_lang, _rinfo.get("en"))
                        for _sid in _sids:
                            _sid_cmt = _auto_comments.get(_sid, _cmt)
                            apply_status_change([_sid], status="Rejected", reason=_code, comment=_sid_cmt, flag=_flag, is_manual=True, is_zip=False)
                        _total += len(_sids)
                    st.session_state.main_toasts.append(f"Rejected {_total} product(s)")
                    st.session_state.main_bridge_counter += 1
                    st.session_state.do_scroll_top = False
                    st.rerun(scope="fragment")
            elif _msg.get("action") == "undo":
                _payload = _msg.get("payload", {})
                _total_restored = 0
                if isinstance(_payload, dict):
                    for _sid in _payload.keys():
                        restore_single_item(_sid)
                        _total_restored += 1
                if _total_restored > 0:
                    st.session_state.main_bridge_counter += 1
                    st.session_state.do_scroll_top = False
                    st.rerun(scope="fragment")
            elif _msg.get("action") == "grid_sort_issue":
                st.session_state.grid_sort_issue = _msg.get("payload", "")
                st.session_state.main_bridge_counter += 1
                st.rerun()
            elif _msg.get("action") == "grid_filter_flag":
                st.session_state.grid_filter_flag = _msg.get("payload", "")
                st.session_state.main_bridge_counter += 1
                st.rerun()
        except Exception as _e:
            logger.error(f"Bridge parse error: {_e}")
"""

start_idx = text.find('@st.fragment\\ndef handle_jtbridge():')
if start_idx == -1:
    start_idx = text.find('@st.fragment\\n\\ndef handle_jtbridge():')
if start_idx == -1:
    start_idx = text.find('def handle_jtbridge():')
    if start_idx != -1: start_idx -= 13 # account for @st.fragment

end_idx = text.find('logger.error(f"Bridge parse error: {_e}")')
if end_idx != -1:
    end_idx += len('logger.error(f"Bridge parse error: {_e}")')

if start_idx != -1 and end_idx != -1:
    new_text = text[:start_idx] + good_handle_jtbridge + text[end_idx:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print("Successfully restored handle_jtbridge.")
else:
    print("Could not find indices! start_idx:", start_idx, "end_idx:", end_idx)
