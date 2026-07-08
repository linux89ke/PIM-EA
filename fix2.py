import re
import json

path = 'c:/Users/Charles.Kireki/Desktop/pim2026-test - Copy - Copy (2) - work 1st 7/streamlit_app.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if '_url = _url.strip().replace("http://", "https://", 1)' in line:
        start_idx = i
    if 'logger.error(f"Bridge parse error: {_e}")' in line:
        end_idx = i

if start_idx != -1 and end_idx != -1:
    good_lines = [
        '                                    if _url.startswith("https"): _warm_urls.add(_url)\n',
        '                            st.session_state["_grid_warm_urls"] = list(_warm_urls)\n',
        '                        except Exception as _pw_err:\n',
        '                            logger.warning("Grid pre-warm failed: %s", _pw_err)\n\n',
        '                        _rej_count = int(final_report[final_report["Status"] == "Rejected"].shape[0])\n',
        '                        _app_count = int(final_report[final_report["Status"] == "Approved"].shape[0])\n',
        '                        _status.update(label=f"Done — {_app_count:,} approved, {_rej_count:,} rejected", state="complete", expanded=False)\n\n',
        '            except Exception as e:\n',
        '                st.error(f"Processing error: {e}")\n',
        '                st.code(traceback.format_exc())\n',
        '                st.session_state.last_processed_files = "error"\n\n',
        '@st.fragment\n',
        'def handle_jtbridge():\n',
        '    _bridge_val = st.text_input(\n',
        '        "jtbridge",\n',
        '        value="",\n',
        '        placeholder="JTBRIDGE_UNIQUE_DO_NOT_USE",\n',
        '        key=f"main_bridge_{st.session_state.main_bridge_counter}",\n',
        '        label_visibility="collapsed",\n',
        '    )\n\n',
        '    if _bridge_val:\n',
        '        try:\n',
        '            _msg = json.loads(_bridge_val)\n',
        '            if _msg.get("action") == "reject_comments":\n',
        '                _ac = _msg.get("payload", {})\n',
        '                if isinstance(_ac, dict):\n',
        '                    if "pending_auto_comments" not in st.session_state: st.session_state.pending_auto_comments = {}\n',
        '                    st.session_state.pending_auto_comments.update(_ac)\n',
        '            elif _msg.get("action") == "reject":\n',
        '                _payload = _msg.get("payload", {})\n',
        '                _auto_comments = st.session_state.pop("pending_auto_comments", {})\n',
        '                if isinstance(_payload, dict) and _payload:\n',
        '                    _rgroups = {}\n',
        '                    for _sid, _rkey in _payload.items(): _rgroups.setdefault(_rkey, []).append(_sid)\n',
        '                    _total = 0\n',
        '                    for _rkey, _sids in _rgroups.items():\n',
        '                        if _rkey.startswith("Other Reason (Custom): "):\n',
        '                            _flag = "Other Reason (Custom)"\n',
        '                            _code = "1000007 - Other Reason"\n',
        '                            _cmt = _rkey.split(": ", 1)[1]\n',
        '                        else:\n',
        '                            _IMAGE_FLAG_FALLBACK = {"REJECT_IMG_STRETCHED": "Image Stretched", "REJECT_IMG_BLURRY": "Image Blurry", "REJECT_IMG_MISMATCH": "Image Mismatch", "REJECT_IMG_INFRINGING": "Image Infringing", "REJECT_IMG_TOO_MANY": "Image Too Many things displayed"}\n',
        '                            _flag = REASON_MAP.get(_rkey) or _IMAGE_FLAG_FALLBACK.get(_rkey, "Other Reason (Custom)")\n',
        '                            _rinfo = support_files["flags_mapping"].get(_flag, {"reason": "1000007 - Other Reason", "en": "Manual rejection"})\n',
        '                            _code = _rinfo["reason"]\n',
        '                            _cmt_lang = "fr" if st.session_state.selected_country == "Morocco" else "en"\n',
        '                            _cmt = _rinfo.get(_cmt_lang, _rinfo.get("en"))\n',
        '                        for _sid in _sids:\n',
        '                            _sid_cmt = _auto_comments.get(_sid, _cmt)\n',
        '                            apply_status_change([_sid], status="Rejected", reason=_code, comment=_sid_cmt, flag=_flag, is_manual=True, is_zip=False)\n',
        '                        _total += len(_sids)\n',
        '                    st.session_state.main_toasts.append(f"Rejected {_total} product(s)")\n',
        '                    st.session_state.main_bridge_counter += 1\n',
        '                    st.session_state.do_scroll_top = False\n',
        '                    st.rerun(scope="fragment")\n',
        '            elif _msg.get("action") == "undo":\n',
        '                _payload = _msg.get("payload", {})\n',
        '                _total_restored = 0\n',
        '                if isinstance(_payload, dict):\n',
        '                    for _sid in _payload.keys():\n',
        '                        restore_single_item(_sid)\n',
        '                        _total_restored += 1\n',
        '                if _total_restored > 0:\n',
        '                    st.session_state.main_bridge_counter += 1\n',
        '                    st.session_state.do_scroll_top = False\n',
        '                    st.rerun(scope="fragment")\n',
        '            elif _msg.get("action") == "grid_sort_issue":\n',
        '                st.session_state.grid_sort_issue = _msg.get("payload", "")\n',
        '                st.session_state.main_bridge_counter += 1\n',
        '                st.rerun()\n',
        '            elif _msg.get("action") == "grid_filter_flag":\n',
        '                st.session_state.grid_filter_flag = _msg.get("payload", "")\n',
        '                st.session_state.main_bridge_counter += 1\n',
        '                st.rerun()\n',
        '        except Exception as _e:\n',
        '            logger.error(f"Bridge parse error: {_e}")\n'
    ]
    new_lines = lines[:start_idx + 1] + good_lines + lines[end_idx + 1:]
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("SUCCESS! handle_jtbridge restored successfully.")
else:
    print(f"FAIL. start_idx: {start_idx}, end_idx: {end_idx}")
