import re
import os

files = ['ui_components.py', 'streamlit_app.py', 'targeted_audit.py', 'targeted_audit_filters.py']
for file in files:
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = re.sub(r'width\s*=\s*[\'"]stretch[\'"]', 'width='stretch'', content)
        # Fix st.image
        new_content = new_content.replace('st.image(img_url, width='stretch')', 'st.image(img_url, use_column_width=True)')
        if new_content != content:
            with open(file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f'Updated {file}')
