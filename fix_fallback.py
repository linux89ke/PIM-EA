import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\New\Desktop\pim2026-test - Copy - Copy (2)\streamlit_app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Lines are 0-indexed, so line 3636 = index 3635, line 3637 = index 3636
i = 3635  # "                    if not all_dfs:\n"
j = 3636  # "                        raise ValueError(...)\n"

assert 'if not all_dfs:' in lines[i], f"Unexpected: {repr(lines[i])}"
assert 'raise ValueError' in lines[j], f"Unexpected: {repr(lines[j])}"

fallback = [
    '                    if not all_dfs:\n',
    '                        # Fallback: if only a ZIP/QC file was uploaded with no separate product file,\n',
    '                        # use the QC results themselves as product data (original behaviour)\n',
    '                        _qr = st.session_state.get("zip_qc_results")\n',
    '                        if _qr is not None and not _qr.empty:\n',
    '                            all_dfs.append(_qr.copy())\n',
    '                        else:\n',
    '                            raise ValueError("No data could be read from the uploaded file(s).")\n',
]

lines[i:j+1] = fallback

with open(r'C:\Users\New\Desktop\pim2026-test - Copy - Copy (2)\streamlit_app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("DONE - fallback inserted successfully at lines 3636-3643")
