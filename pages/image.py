"""
Bulk Image Downloader (Streamlit App)
--------------------------------------
- Upload an XLSX file with a SKU column and URL1, URL2, URL3, ... columns
- Downloads every image URL
- Renames each file using SKU + the URL column's position, e.g. the image
  in URL3 for SKU ABC123 becomes ABC123_3
- Optional: convert transparent PNGs to JPEG with a white background
- Download everything as a single ZIP, or download files one by one

Run with:
    streamlit run bulk_image_downloader.py
"""

import io
import zipfile
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from PIL import Image

st.set_page_config(page_title="Bulk Image Downloader", page_icon="🖼️", layout="wide")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def guess_extension(url: str, content_type: str | None) -> str:
    """Best-effort guess of the file extension for a downloaded image."""
    path = urlparse(url).path
    if "." in path.rsplit("/", 1)[-1]:
        ext = path.rsplit(".", 1)[-1].lower().split("?")[0]
        if 1 <= len(ext) <= 5:
            return ext
    if content_type:
        content_type = content_type.lower()
        if "jpeg" in content_type or "jpg" in content_type:
            return "jpg"
        if "png" in content_type:
            return "png"
        if "webp" in content_type:
            return "webp"
        if "gif" in content_type:
            return "gif"
        if "bmp" in content_type:
            return "bmp"
    return "jpg"


def has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        alpha = img.getchannel("A")
        return alpha.getextrema()[0] < 255
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def flatten_to_white_jpeg(img: Image.Image) -> bytes:
    """Convert an image with transparency onto a white background and
    return JPEG bytes."""
    img = img.convert("RGBA")
    background = Image.new("RGB", img.size, (255, 255, 255))
    background.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    background.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def download_image(url: str, convert_transparent: bool):
    """Download a single image and return (bytes, extension, error)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        raw_bytes = resp.content
        content_type = resp.headers.get("Content-Type", "")
        ext = guess_extension(url, content_type)

        if convert_transparent:
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                img.load()
                if img.format == "PNG" and has_transparency(img):
                    return flatten_to_white_jpeg(img), "jpg", None
            except Exception:
                # Not a readable image via PIL - fall back to raw bytes
                pass

        return raw_bytes, ext, None
    except Exception as e:
        return None, None, str(e)


def build_zip(results: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            if r["bytes"] is not None:
                zf.writestr(r["filename"], r["bytes"])
    buf.seek(0)
    return buf.getvalue()


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
st.title("🖼️ Bulk Image Downloader")
st.caption(
    "Upload an XLSX with a **SKU** column and one or more **URL1, URL2, URL3, ...** "
    "columns. Each image is renamed using the SKU plus the URL column's position "
    "— e.g. the image in `URL3` for SKU `ABC123` becomes `ABC123_3`."
)

with st.sidebar:
    st.header("Options")
    convert_transparent = st.checkbox(
        "Convert transparent PNGs to JPEG (white background)",
        value=True,
        help="Only affects PNGs that actually have transparent pixels. "
        "Other formats are downloaded as-is.",
    )
    st.divider()
    st.markdown(
        "**XLSX format expected:**\n\n"
        "| SKU | URL1 | URL2 | URL3 |\n|---|---|---|---|\n"
        "| ABC123 | https://.../img1.png | https://.../img2.png | |\n"
        "| XYZ999 | https://.../img3.jpg | | |\n\n"
        "`URL1` also matches a plain `URL` column. Blank cells are skipped. "
        "The number in the column name (1, 2, 3, ...) becomes the filename suffix, "
        "e.g. `URL2` → `SKU_2`."
    )

uploaded_file = st.file_uploader("Upload XLSX file", type=["xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Could not read the Excel file: {e}")
        st.stop()

    # normalize column names (case-insensitive)
    df.columns = [str(c).strip() for c in df.columns]
    col_map = {c.lower(): c for c in df.columns}

    if "sku" not in col_map:
        st.error(f"The file must contain a 'SKU' column. Found: {list(df.columns)}")
        st.stop()

    sku_col = col_map["sku"]

    # find URL columns: URL, URL1, URL2, URL3, ... (case-insensitive)
    import re

    url_cols = []
    for c in df.columns:
        m = re.fullmatch(r"url\s*(\d*)", c.strip(), flags=re.IGNORECASE)
        if m:
            num = int(m.group(1)) if m.group(1) else 1
            url_cols.append((num, c))
    url_cols.sort(key=lambda x: x[0])

    if not url_cols:
        st.error(
            f"The file must contain at least one URL column (e.g. 'URL', 'URL1', 'URL2', ...). "
            f"Found: {list(df.columns)}"
        )
        st.stop()

    rows = []
    for _, row in df.iterrows():
        sku = str(row[sku_col]).strip()
        if not sku or sku.lower() == "nan":
            continue
        for num, col in url_cols:
            url = row.get(col)
            if pd.isna(url):
                continue
            url = str(url).strip()
            if not url or url.lower() == "nan":
                continue
            rows.append({"SKU": sku, "URL": url, "target_name": f"{sku}_{num}"})

    df = pd.DataFrame(rows)

    if df.empty:
        st.error("No valid SKU/URL pairs were found in the file.")
        st.stop()

    dupes = df["target_name"][df["target_name"].duplicated(keep=False)].unique()
    if len(dupes) > 0:
        st.warning(
            f"{len(dupes)} filename(s) collide (same SKU + column position): "
            + ", ".join(dupes[:10])
            + (" ..." if len(dupes) > 10 else "")
        )

    st.subheader("Preview")
    st.dataframe(df[["SKU", "URL", "target_name"]], use_container_width=True, height=250)

    st.write(f"**{len(df)}** image URL(s) found across **{df['SKU'].nunique()}** SKU(s).")

    if st.button("🚀 Download all images", type="primary"):
        results = []
        progress = st.progress(0, text="Starting downloads...")
        status_area = st.empty()

        for i, row in enumerate(df.itertuples(index=False)):
            progress.progress(
                (i + 1) / len(df), text=f"Downloading {i + 1}/{len(df)}: {row.target_name}"
            )
            img_bytes, ext, error = download_image(row.URL, convert_transparent)
            filename = f"{row.target_name}.{ext}" if ext else f"{row.target_name}.bin"
            results.append(
                {
                    "sku": row.SKU,
                    "url": row.URL,
                    "filename": filename,
                    "bytes": img_bytes,
                    "error": error,
                }
            )

        progress.progress(1.0, text="Done!")
        st.session_state["download_results"] = results

if "download_results" in st.session_state:
    results = st.session_state["download_results"]
    ok = [r for r in results if r["bytes"] is not None]
    failed = [r for r in results if r["bytes"] is None]

    st.divider()
    st.subheader("Results")
    c1, c2 = st.columns(2)
    c1.metric("✅ Successful", len(ok))
    c2.metric("❌ Failed", len(failed))

    if failed:
        with st.expander(f"Show {len(failed)} failed download(s)"):
            st.dataframe(
                pd.DataFrame([{"SKU": r["sku"], "URL": r["url"], "Error": r["error"]} for r in failed]),
                use_container_width=True,
            )

    if ok:
        zip_bytes = build_zip(ok)
        st.download_button(
            "⬇️ Download all as ZIP",
            data=zip_bytes,
            file_name="images.zip",
            mime="application/zip",
            type="primary",
        )

        st.markdown("#### Download individually")
        search = st.text_input("Filter by SKU / filename", "")
        filtered = [r for r in ok if search.lower() in r["filename"].lower()] if search else ok

        for r in filtered:
            col1, col2, col3 = st.columns([1, 3, 2])
            with col1:
                try:
                    st.image(r["bytes"], width=60)
                except Exception:
                    st.write("—")
            with col2:
                st.write(r["filename"])
                st.caption(r["url"])
            with col3:
                st.download_button(
                    "Download",
                    data=r["bytes"],
                    file_name=r["filename"],
                    mime="image/jpeg" if r["filename"].endswith(".jpg") else "application/octet-stream",
                    key=f"dl_{r['filename']}",
                )
else:
    st.info("Upload an XLSX file to get started.")
