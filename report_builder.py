"""
Generates a Word (.docx) audit report from the unified targeted-audit results
(evaluate_all_checks() output), in the same structure as the reference
"QC Audit Report" template:

  1. Dashboard        — total reviewed / approval rate / rejection rate
  2. Overview          — one-paragraph summary
  3. Issues Found       — one "Issue N" section per (check, reason type) that
     needs attention (False Approval / False Rejection / AI Error / Needs
     Manual Review), each with its own findings table. Image extraction
     failures and duplicates are just another check in this same loop —
     nothing lives in a separate disconnected list.

Pure python-docx, no network calls — built entirely from the DataFrame
produced by evaluate_all_checks().
"""
from io import BytesIO
from datetime import date

import pandas as pd
from docx import Document
from docx.shared import Pt, Inches
from docx.shared import RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from targeted_audit_filters import CHECK_ORDER, CHECK_LABELS

_NAVY = RGBColor(0x1F, 0x4E, 0x78)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
# Which verdicts actually get their own "Issue" section in the report —
# True Rejection / Skipped are correct pipeline behaviour, so they're only
# counted in the Overview, not tabled individually.
_REPORTABLE_VERDICTS = ["False Approval", "False Rejection", "Needs Manual Review", "AI Error"]

_VERDICT_INTRO = {
    "False Approval": "were approved by the pipeline, but the file's own mandatory-attribute "
                       "rule for this category says they should not have been. For color checks, "
                       "this means the product was approved despite having no value in the COLOR "
                       "column (COLOR_FAMILY alone or color mentioned only in the product name "
                       "does not count as a valid color declaration).",
    "False Rejection": "were rejected, but re-checking against the file's own rule for this "
                        "category shows the rejection was incorrect. For color checks, this means "
                        "the product was rejected for missing color, but the COLOR column already "
                        "had a specific color value declared, or color was not mandatory for this "
                        "category.",
    "Needs Manual Review": "cannot be resolved automatically from the data alone and require a "
                            "human to review — often because they depend on visual inspection or "
                            "a subjective AI suggestion.",
    "AI Error": "could not be properly evaluated because the check itself failed (e.g. a gateway "
                "timeout or bad response) rather than returning a real finding.",
}


def _shade_cell(cell, hex_color: str):
    shd = OxmlElement('w:shd')
    shd.set(qn('w:fill'), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _clean(val) -> str:
    if val is None:
        return ""
    s = str(val)
    return "" if s.strip().lower() in ("nan", "none") else s


def _content_cols(df: pd.DataFrame, exclude: tuple) -> list:
    """Only columns that actually have data in this slice — since every check
    shares one combined results DataFrame, an unrelated check's column would
    otherwise show up entirely empty (e.g. FDA on a Color-check row)."""
    cols = []
    for c in df.columns:
        if c in exclude:
            continue
        vals = df[c].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
        if vals.ne("").any():
            cols.append(c)
    return cols


# Explicit, deliberately minimal column set per check for the printed report —
# SKU / Name / Category always, plus at most one or two fields that are
# actually the subject of that check, plus Detail last. Raw image URLs are
# left out of the Word doc (not actionable in print); they're still shown as
# thumbnails in the app itself.
_DOC_EXTRA_FIELDS = {
    "skip": [],
    "duplicate": [],
    "category": ["Suggested Categories", "Top1 Category", "Category Match Score",
                 "Top1 Score", "Initial Category Path"],
    "color": ["Color", "Color (AI Normalized)", "Color Family"],
    "warranty": ["Warranty", "Warranty Type", "Warranty Duration", "Warranty Address"],
    "variation": ["Variation", "Existing Variation Count"],
    "fda": ["FDA"],
    "title_language": [],
    "name_brand": ["Brand", "Brand Detected On Product"],
    "image_quality": ["Image Filename"],
    "image_extraction": ["Image"],
    "ai_caption": ["Image"],
    "brand_image": ["Listed Brand", "Brand Detected On Product"],
}


def _doc_columns(check_key: str, df: pd.DataFrame) -> list:
    base = [c for c in ("ProductSetSid", "Product Name", "Seller", "Category") if c in df.columns]
    extra = [c for c in _DOC_EXTRA_FIELDS.get(check_key, []) if c in df.columns]
    # Still drop a whitelisted column if it happens to be empty for this
    # particular slice (e.g. Brand blank on some row) — no point in a
    # column of nothing.
    extra = [c for c in extra if _content_cols(df[[c]], exclude=())]
    tail = [c for c in ("Detail",) if c in df.columns]
    return base + extra + tail


def _add_table(doc: Document, headers: list, rows: list, widths_in: list = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    # Wide tables (many context columns) get a slightly smaller font so
    # nothing gets squeezed unreadably thin.
    font_size = Pt(9) if len(headers) <= 6 else Pt(7.5)

    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = ""
        run = hdr_cells[i].paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = font_size
        run.font.color.rgb = _WHITE
        _shade_cell(hdr_cells[i], "1F4E78")

    for row_vals in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row_vals):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(_clean(val))
            run.font.size = font_size

    if widths_in:
        for row in table.rows:
            for cell, w in zip(row.cells, widths_in):
                cell.width = Inches(w)
    return table


# Relative column widths (inches, ~6.5in usable page width) so a UUID-style
# SKU doesn't eat the same space as a long Detail explanation.
_COL_WIDTHS = {
    "ProductSetSid": 0.9,
    "Product Name": 1.3,
    "Category": 0.9,
    "Color": 0.6,
    "Color Family": 0.7,
    "Color (AI Normalized)": 0.8,
    "Warranty": 0.7,
    "Warranty Type": 0.7,
    "Warranty Duration": 0.7,
    "Warranty Address": 0.9,
    "Variation": 0.8,
    "Existing Variation Count": 0.7,
    "FDA": 0.7,
    "Brand": 0.7,
    "Listed Brand": 0.7,
    "Brand Detected On Product": 0.9,
    "Seller": 0.7,
    "Suggested Categories": 1.1,
    "Top1 Category": 0.9,
    "Category Match Score": 0.6,
    "Top1 Score": 0.6,
    "Initial Category Path": 0.9,
    "Image Filename": 0.9,
    "Image": 1.2,
    "Detail": 1.6,
}


def _widths_for(headers: list) -> list:
    raw = [_COL_WIDTHS.get(h, 0.8) for h in headers]
    total = sum(raw)
    target = 6.5
    return [round(w / total * target, 2) for w in raw]


def _dashboard_table(doc: Document, total: int, approval_rate: str, rejection_rate: str):
    dash = doc.add_table(rows=2, cols=3)
    dash.style = "Table Grid"
    dash.alignment = WD_TABLE_ALIGNMENT.CENTER
    big_vals = [f"{total:,}", approval_rate, rejection_rate]
    labels = ["Total Products Reviewed", "Approval Rate", "Rejection Rate"]
    for i in range(3):
        top = dash.rows[0].cells[i]
        top.text = ""
        r = top.paragraphs[0].add_run(big_vals[i])
        r.bold = True
        r.font.size = Pt(20)
        top.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        bottom = dash.rows[1].cells[i]
        bottom.text = ""
        r2 = bottom.paragraphs[0].add_run(labels[i])
        r2.font.size = Pt(9)
        bottom.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER


def build_docx_report(
    fr: pd.DataFrame,
    results: pd.DataFrame,
    country_label: str = "",
) -> bytes:
    """Returns the .docx file as raw bytes, ready for a Streamlit download_button."""
    doc = Document()

    # ── Title block ────────────────────────────────────────────────────────
    title_p = doc.add_paragraph()
    title_run = title_p.add_run(f"QC AUDIT REPORT {country_label.upper()}".strip())
    title_run.bold = True
    title_run.font.size = Pt(20)
    title_run.font.color.rgb = _NAVY

    subtitle_p = doc.add_paragraph()
    subtitle_run = subtitle_p.add_run(f"Prepared by QC Team | {date.today():%d %B %Y}")
    subtitle_run.italic = True
    subtitle_run.font.size = Pt(11)

    # ── 1. Dashboard ───────────────────────────────────────────────────────
    doc.add_heading("1. Dashboard", level=1)
    total = len(fr) if fr is not None and not fr.empty else 0
    if total and "Status" in fr.columns:
        approved = int((fr["Status"] == "Approved").sum())
        rejected = int((fr["Status"] == "Rejected").sum())
    else:
        approved = 0
        rejected = 0
    approval_rate = f"{approved / total * 100:.1f}%" if total else "N/A"
    rejection_rate = f"{rejected / total * 100:.1f}%" if total else "N/A"
    _dashboard_table(doc, total, approval_rate, rejection_rate)
    doc.add_paragraph()

    # ── 2. Overview ────────────────────────────────────────────────────────
    doc.add_heading("2. Overview", level=1)
    counts = results["Verdict"].value_counts() if results is not None and not results.empty else pd.Series(dtype=int)
    n_false_approvals = int(counts.get("False Approval", 0))
    n_false_rej = int(counts.get("False Rejection", 0))
    n_true_rej = int(counts.get("True Rejection", 0))
    n_manual = int(counts.get("Needs Manual Review", 0))
    n_ai_error = int(counts.get("AI Error", 0))
    n_dup = int(counts.get("Duplicate", 0))
    n_skip = int(counts.get("Skipped", 0))

    doc.add_paragraph(
        f"The rule-based QC validation system evaluated {total:,} products, approving {approved:,} "
        f"and rejecting {rejected:,}. This targeted audit re-examined every one of the file's own "
        f"per-check decisions, confirming {n_true_rej:,} rejections were correct. It identified "
        f"{n_false_approvals:,} potential false approvals and {n_false_rej:,} potential false "
        f"rejections requiring attention, plus {n_manual:,} case(s) needing manual review and "
        f"{n_ai_error:,} case(s) where a check itself failed rather than returning a real result "
        f"(including AI/gateway errors and image extraction failures such as broken connections "
        f"or timeouts). Separately, {n_dup:,} product(s) were flagged as duplicates and {n_skip:,} "
        f"were excluded before QC (prohibited/inactive/tester categories)."
    )

    # ── 3. Issues Found ────────────────────────────────────────────────────
    doc.add_heading("3. Issues Found", level=1)
    issue_num = 1
    any_issue = False

    if results is not None and not results.empty:
        for check_key in CHECK_ORDER:
            label = CHECK_LABELS[check_key]
            check_slice = results[results["Check"] == check_key]
            if check_slice.empty:
                continue

            for reason_type in check_slice["Reason Type"].unique():
                reason_slice = check_slice[check_slice["Reason Type"] == reason_type]

                for verdict in _REPORTABLE_VERDICTS:
                    if check_key == "category" and verdict != "AI Error":
                        continue
                    sub = reason_slice[reason_slice["Verdict"] == verdict]
                    if sub.empty:
                        continue
                    any_issue = True

                    doc.add_heading(
                        f"Issue {issue_num} — {label}: {reason_type} ({verdict}, {len(sub)} Cases)",
                        level=2,
                    )
                    issue_num += 1
                    doc.add_paragraph(f"These {len(sub)} product(s) {_VERDICT_INTRO[verdict]}")
                    cols = _doc_columns(check_key, sub)
                    _add_table(doc, cols, sub[cols].values.tolist(), widths_in=_widths_for(cols))
                    doc.add_paragraph()

    if not any_issue:
        doc.add_paragraph("No false approvals, false rejections, AI errors, or manual-review "
                           "cases were found across any check.")

    # ── Infrastructure failures (image extraction) are now covered
    # automatically above — they flow through as an "image_extraction"
    # check with Verdict "AI Error", same as any other check.

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()