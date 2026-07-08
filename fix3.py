import re

path = 'c:/Users/Charles.Kireki/Desktop/pim2026-test - Copy - Copy (2) - work 1st 7/ui_components.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# We need to find where the error was made:
# I accidentally inserted:
# """
#     
#     def _sel(val, curr): return "selected" if val == curr else ""
#     sort_html = f"""
# ...
#     skeleton_html += sort_html + filter_html + """
# </div>

start_str = '  <button class="batch-btn top-btn" onclick="window.scrollTo(0, document.body.scrollHeight)">{_t("go_bottom")}</button>\n"""\n    \n    def _sel(val, curr): return "selected" if val == curr else ""\n    sort_html = f"""\n'
end_str = '    <option value="Product Name Brand Name" {_sel(\'Product Name Brand Name\', curr_flag)}>Name/Brand Check</option>\n  </select>\n"""\n    skeleton_html += sort_html + filter_html + """\n</div>\n'

start_idx = text.find(start_str)
end_idx = text.find(end_str)

if start_idx != -1 and end_idx != -1:
    end_idx += len(end_str)
    
    # We will replace this block with just the variables injected!
    # Wait, sort_html and filter_html must be defined BEFORE the return statement!
    
    # Let's find the return statement
    return_idx = text.find('    return f"""<!DOCTYPE html>')
    
    # Let's prepare sort_html and filter_html
    sort_filter_code = """
    def _sel(val, curr): return "selected" if val == curr else ""
    sort_html = f'''
  <select class="reason-sel sort-sel" id="sort-sel-top" onchange="sendMsg('grid_sort_issue', this.value)" style="max-width:170px;" title="{labels_dict.get('sort_by_issue', 'Sort')}">
    <option value="" {_sel('', curr_sort)}>{labels_dict.get('sort_by_issue', 'Sort')}</option>
    <option value="most_flagged" {_sel('most_flagged', curr_sort)}>{labels_dict.get('most_flagged', 'Most Flagged')}</option>
    <option value="no_issue" {_sel('no_issue', curr_sort)}>{labels_dict.get('no_issue_first', 'No Issue')}</option>
    <option disabled>── {labels_dict.get('grp_image', 'Image')} ──</option>
    <option value="low_res" {_sel('low_res', curr_sort)}>{labels_dict.get('sort_low_res', 'Low Res')}</option>
    <option value="tall" {_sel('tall', curr_sort)}>{labels_dict.get('sort_tall', 'Tall')}</option>
    <option value="wide" {_sel('wide', curr_sort)}>{labels_dict.get('sort_wide', 'Wide')}</option>
    <option value="broken" {_sel('broken', curr_sort)}>{labels_dict.get('sort_broken', 'Broken')}</option>
    <option disabled>── {labels_dict.get('grp_qc_flags', 'QC')} ──</option>
    <option value="Wrong Category" {_sel('Wrong Category', curr_sort)}>{labels_dict.get('sort_wrong_cat', 'Wrong Cat')}</option>
    <option value="Restricted brands" {_sel('Restricted brands', curr_sort)}>{labels_dict.get('sort_restr_brand', 'Restricted')}</option>
    <option value="Suspected Fake product" {_sel('Suspected Fake product', curr_sort)}>{labels_dict.get('sort_fake', 'Fake')}</option>
    <option value="Missing COLOR" {_sel('Missing COLOR', curr_sort)}>{labels_dict.get('sort_missing_color', 'Color')}</option>
    <option value="Product Warranty" {_sel('Product Warranty', curr_sort)}>{labels_dict.get('sort_warranty', 'Warranty')}</option>
    <option value="Duplicate product" {_sel('Duplicate product', curr_sort)}>{labels_dict.get('sort_duplicates', 'Duplicate')}</option>
    <option disabled>── {labels_dict.get('grp_prefetch', 'Prefetch')} ──</option>
    <option value="Category Check" {_sel('Category Check', curr_sort)}>Category Check</option>
    <option value="Warranty Check" {_sel('Warranty Check', curr_sort)}>Warranty Check</option>
    <option value="FDA" {_sel('FDA', curr_sort)}>FDA</option>
    <option value="Color Check" {_sel('Color Check', curr_sort)}>Color Check</option>
    <option value="Variation Check" {_sel('Variation Check', curr_sort)}>Variation Check</option>
    <option value="Brand Image Check" {_sel('Brand Image Check', curr_sort)}>Brand Image Check</option>
    <option value="Title Language Check" {_sel('Title Language Check', curr_sort)}>Title Language Check</option>
    <option value="Image Quality Check" {_sel('Image Quality Check', curr_sort)}>Image Quality Check</option>
  </select>
'''
    filter_html = f'''
  <select class="reason-sel sort-sel" id="filter-sel-top" onchange="sendMsg('grid_filter_flag', this.value)" style="max-width:180px;" title="{labels_dict.get('filter_by_flag', 'Filter')}">
    <option value="" {_sel('', curr_flag)}>{labels_dict.get('filter_by_flag', 'Filter')}</option>
    <option value="brand_ocr" {_sel('brand_ocr', curr_flag)}>{labels_dict.get('filter_brand_ocr', 'Brand OCR')}</option>
    <option value="duplicates" {_sel('duplicates', curr_flag)}>{labels_dict.get('filter_duplicates', 'Duplicates')}</option>
    <option value="manual_review" {_sel('manual_review', curr_flag)}>{labels_dict.get('filter_manual', 'Manual Review')}</option>
    <option value="color_mismatch" {_sel('color_mismatch', curr_flag)}>{labels_dict.get('filter_color_mis', 'Color Mis')}</option>
    <option value="committed" {_sel('committed', curr_flag)}>{labels_dict.get('all_rejected', 'All Rejected')}</option>
    <option value="no_flags" {_sel('no_flags', curr_flag)}>{labels_dict.get('clean_no_flags', 'Clean')}</option>
    <option disabled>── {labels_dict.get('grp_qc_flags', 'QC')} ──</option>
    <option value="Wrong Category" {_sel('Wrong Category', curr_flag)}>{labels_dict.get('sort_wrong_cat', 'Wrong Cat')}</option>
    <option value="Restricted brands" {_sel('Restricted brands', curr_flag)}>{labels_dict.get('sort_restr_brand', 'Restricted')}</option>
    <option value="Suspected Fake product" {_sel('Suspected Fake product', curr_flag)}>{labels_dict.get('sort_fake', 'Fake')}</option>
    <option value="Missing COLOR" {_sel('Missing COLOR', curr_flag)}>{labels_dict.get('sort_missing_color', 'Color')}</option>
    <option value="Product Warranty" {_sel('Product Warranty', curr_flag)}>{labels_dict.get('sort_warranty', 'Warranty')}</option>
    <option value="Duplicate product" {_sel('Duplicate product', curr_flag)}>{labels_dict.get('sort_duplicates', 'Duplicate')}</option>
    <option value="BRAND name repeated in NAME" {_sel('BRAND name repeated in NAME', curr_flag)}>{labels_dict.get('filter_brand_name', 'Brand Name')}</option>
    <option value="Unnecessary words" {_sel('Unnecessary words', curr_flag)}>{labels_dict.get('filter_unneeded', 'Unneeded')}</option>
    <option value="Prohibited Words" {_sel('Prohibited Words', curr_flag)}>{labels_dict.get('filter_prohibited', 'Prohibited')}</option>
    <option disabled>── {labels_dict.get('grp_prefetch', 'Prefetch')} ──</option>
    <option value="Category Check" {_sel('Category Check', curr_flag)}>Category Check</option>
    <option value="Warranty Check" {_sel('Warranty Check', curr_flag)}>Warranty Check</option>
    <option value="FDA" {_sel('FDA', curr_flag)}>FDA</option>
    <option value="Color Check" {_sel('Color Check', curr_flag)}>Color Check</option>
    <option value="Variation Check" {_sel('Variation Check', curr_flag)}>Variation Check</option>
    <option value="Brand Image Check" {_sel('Brand Image Check', curr_flag)}>Brand Image Check</option>
    <option value="Title Language Check" {_sel('Title Language Check', curr_flag)}>Title Language Check</option>
    <option value="Image Quality Check" {_sel('Image Quality Check', curr_flag)}>Image Quality Check</option>
    <option value="Product Name Brand Name" {_sel('Product Name Brand Name', curr_flag)}>Name/Brand Check</option>
  </select>
'''
"""
    
    replacement_str = '  <button class="batch-btn top-btn" onclick="window.scrollTo(0, document.body.scrollHeight)">{_t("go_bottom")}</button>\n  {sort_html}\n  {filter_html}\n</div>\n'
    
    # Clean up the broken block
    text = text[:start_idx] + replacement_str + text[end_idx:]
    
    # Inject variables before return statement
    return_idx = text.find('    return f"""<!DOCTYPE html>')
    text = text[:return_idx] + sort_filter_code + text[return_idx:]
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print("SUCCESS: Fixed ui_components.py syntax error")
else:
    print("FAIL: Could not find block to replace.")
    print("Start idx:", start_idx)
    print("End idx:", end_idx)
