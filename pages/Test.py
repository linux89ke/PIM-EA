import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import streamlit as st
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import re, json, zipfile, os
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse, quote_plus, quote, unquote
import urllib.request

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

st.set_page_config(page_title="Decathlon Image Grabber", page_icon="🛒", layout="wide")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}
MAX_PX       = 1000
WORKERS      = 12
PRODUCT_CDN  = "contents.mediadecathlon.com"
REJECT_PATHS = {
    "/brand/", "/logo/", "/icon/", "/banner/", "/cms/", "/category/",
    "/sport/", "/picto/", "/flag/", "/badge/", "/avatar/", "/store/",
    "/editorial/", "/push/", "/highlight/", "/advice/",
}
MIN_DIMENSION = 200
IMAGE_COLS = ["image1","image2","picture_2","picture_3","picture_4",
              "picture_5","picture_6","picture_7","picture_8","picture_9","picture_10"]

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
adapter = requests.adapters.HTTPAdapter(pool_connections=WORKERS, pool_maxsize=WORKERS)
SESSION.mount("https://", adapter)
SESSION.mount("http://",  adapter)


# ── Playwright check ──────────────────────────────────────────────────────────
def check_playwright_working() -> tuple[bool, str]:
    if not PLAYWRIGHT_AVAILABLE:
        return False, "Playwright library is not installed."
    try:
        pw_browsers = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
        if os.path.isdir(pw_browsers):
            chromium_dirs = [d for d in os.listdir(pw_browsers) if d.startswith("chromium")]
            if chromium_dirs:
                return True, f"Found chromium: {chromium_dirs[-1]}"
            return False, f"No chromium folder in {pw_browsers}."
        return False, f"Playwright browser directory not found at {pw_browsers}."
    except Exception as e:
        return False, f"Could not verify: {e}"


# ── URL helpers ───────────────────────────────────────────────────────────────
def is_product_image_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if PRODUCT_CDN not in p.netloc:
        return False
    if not re.search(r'\.(jpg|jpeg|png|webp|avif)$', p.path, re.I):
        return False
    if any(seg in p.path.lower() for seg in REJECT_PATHS):
        return False
    return True


def canonical_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def extract_slug(url_or_title: str) -> str:
    if url_or_title.startswith("http"):
        try:
            path = urlparse(url_or_title).path
            segs = [s for s in path.split("/") if s]
            if segs:
                seg = re.sub(r'\.html$', '', segs[-1], flags=re.I)
                seg = re.sub(r'[^a-zA-Z0-9\-_]', '_', seg)
                seg = re.sub(r'_{2,}', '_', seg).strip('_')
                return seg if seg else "product"
        except Exception:
            pass
        return "product"
    slug = url_or_title.strip().lower()
    slug = re.sub(r'\s+', '_', slug)
    slug = re.sub(r'[^a-zA-Z0-9\-_]', '', slug)
    slug = re.sub(r'_{2,}', '_', slug).strip('_')
    return slug if slug else "product"


# ── Excel loading ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_excel(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_excel(BytesIO(file_bytes))
    return df


def get_excel_image_urls(row: pd.Series, max_images: int) -> list[str]:
    """Pull image URLs from the Excel row — much faster than scraping."""
    urls = []
    seen = set()
    for col in IMAGE_COLS:
        val = row.get(col)
        if pd.notna(val) and isinstance(val, str) and val.strip():
            url = val.strip()
            # Normalise the URL — strip query params for canonical form
            canon = canonical_url(url)
            if canon not in seen and PRODUCT_CDN in url:
                seen.add(canon)
                # Prefer clean URLs, fall back to original
                clean = url.split("?")[0]
                urls.append(clean)
        if len(urls) >= max_images:
            break
    return urls


# ── Download helpers ──────────────────────────────────────────────────────────
def _download_one(url: str) -> tuple[str, Image.Image] | None:
    try:
        r = SESSION.get(url, timeout=12)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content))
        if img.width < MIN_DIMENSION or img.height < MIN_DIMENSION:
            return None
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.width > MAX_PX or img.height > MAX_PX:
            img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
        return url, img
    except Exception:
        return None


def download_all_parallel(urls: list[str]) -> list[Image.Image]:
    results: dict[str, Image.Image] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_download_one, u): u for u in urls}
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                results[res[0]] = res[1]
    return [results[u] for u in urls if u in results]


def to_jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()


def make_bulk_zip(products: list[dict]) -> bytes:
    buf = BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for p in products:
            for fname, data, _ in p["images"]:
                base, ext = os.path.splitext(fname)
                candidate = fname
                counter = 1
                while candidate in seen_names:
                    candidate = f"{base}_{counter}{ext}"
                    counter += 1
                seen_names.add(candidate)
                zf.writestr(candidate, data)
    return buf.getvalue()


# ── Web scraping (fallback / URL mode) ───────────────────────────────────────
def fetch_html_requests(url: str) -> str:
    r = SESSION.get(url, timeout=12, allow_redirects=True)
    r.raise_for_status()
    return r.text


def fetch_html_playwright(url: str) -> tuple[str, list[str]]:
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed.")
    intercepted: list[str] = []
    html = ""
    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--blink-settings=imagesEnabled=false"],
            )
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")

            def on_request(request):
                try:
                    u = request.url
                    rt = request.resource_type
                    if is_product_image_url(u):
                        intercepted.append(u)
                    if rt in ("font", "media", "websocket") or "analytics" in u or "tracking" in u:
                        request.abort()
                    else:
                        request.continue_()
                except Exception:
                    pass

            page = ctx.new_page()
            page.route("**/*", on_request)
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            page.evaluate("""
                () => {
                    window.scrollTo(0, document.body.scrollHeight / 3);
                    window.scrollTo(0, document.body.scrollHeight * 2 / 3);
                    window.scrollTo(0, document.body.scrollHeight);
                }
            """)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
            html = page.content()
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
    return html, intercepted


def extract_image_urls_from_html(html: str, intercepted: list[str], max_images: int = 3) -> list[str]:
    raw: list[str] = list(intercepted)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("meta", property="og:image"):
        if src := tag.get("content", "").strip():
            raw.append(src)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("image", "images", "thumbnailUrl"):
                    val = item.get(key)
                    if isinstance(val, str):
                        raw.append(val)
                    elif isinstance(val, list):
                        raw.extend(v for v in val if isinstance(v, str))
        except Exception:
            pass
    for script in soup.find_all("script"):
        text = script.string or ""
        if PRODUCT_CDN in text:
            for m in re.findall(r'https://contents\.mediadecathlon\.com/[^\"\' \}\]]+', text):
                raw.append(m)
    for img in soup.find_all("img"):
        for attr in ("data-src", "data-lazy-src", "src"):
            if src := img.get(attr, "").strip():
                raw.append(src)
        for part in img.get("srcset", "").split(","):
            parts = part.strip().split()
            if parts:
                raw.append(parts[0])

    seen_canonical: set[str] = set()
    unique: list[str] = []
    for u in raw:
        u = u.strip()
        if not u or not is_product_image_url(u):
            continue
        canon = canonical_url(u)
        if canon in seen_canonical:
            continue
        seen_canonical.add(canon)
        unique.append(canon)
    return unique[:max_images]


def run_fetch_url(url: str, max_images: int, use_playwright: bool) -> dict | None:
    intercepted: list[str] = []
    html = ""
    if use_playwright:
        try:
            html, intercepted = fetch_html_playwright(url)
        except Exception as e:
            st.warning(f"⚠️ Browser mode failed, falling back to requests: {e}")
            try:
                html = fetch_html_requests(url)
            except Exception as e2:
                st.error(f"❌ Both modes failed: {e2}")
                return None
    else:
        try:
            html = fetch_html_requests(url)
        except Exception as e:
            st.error(f"❌ Fetch failed: {e}")
            return None

    img_urls = extract_image_urls_from_html(html, intercepted, max_images)
    if not img_urls:
        return None
    images = download_all_parallel(img_urls)
    if not images:
        return None
    slug = extract_slug(url)
    return {
        "slug": slug,
        "url": url,
        "images": [(f"{slug}_{i+1:02d}.jpg", to_jpeg_bytes(img), img)
                   for i, img in enumerate(images)]
    }


# ── Decathlon search via Playwright ──────────────────────────────────────────
def search_decathlon_kenya(query: str) -> list[dict]:
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed.")
    search_url = f"https://www.decathlon.co.ke/search?q={quote_plus(query)}"
    results: list[dict] = []
    api_data: list[dict] = []
    html = ""

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")
            page = ctx.new_page()

            def on_response(response):
                try:
                    url = response.url
                    if (("search" in url or "algolia" in url or "catalog" in url) and "decathlon" in url):
                        ct = response.headers.get("content-type", "")
                        if "json" in ct.lower():
                            api_data.append({"url": url, "body": response.json()})
                except Exception:
                    pass

            def on_request(request):
                try:
                    rt = request.resource_type
                    if rt in ("font", "media", "image") or "analytics" in request.url:
                        request.abort()
                    else:
                        request.continue_()
                except Exception:
                    pass

            page.on("response", on_response)
            page.route("**/*", on_request)
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=25_000)
            except Exception:
                pass
            for selector in ["a[href*='/p/']", "[class*='product']", "article"]:
                try:
                    page.wait_for_selector(selector, timeout=5_000)
                    break
                except Exception:
                    continue
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
            html = page.content()
            try:
                links = page.eval_on_selector_all(
                    "a[href*='/p/']",
                    """els => els.map(el => ({
                        url: el.href,
                        title: el.getAttribute('aria-label')
                                 || el.querySelector('[class*="name"],[class*="title"],h2,h3,p')?.textContent?.trim()
                                 || el.textContent?.trim()?.slice(0, 80) || '',
                        thumb: el.querySelector('img')?.src || el.querySelector('img')?.dataset?.src || ''
                    }))"""
                )
                seen: set[str] = set()
                for item in links:
                    url_r = item.get("url", "")
                    if not url_r or url_r in seen or not re.search(r'/p/\d', url_r):
                        continue
                    seen.add(url_r)
                    results.append({
                        "url": url_r,
                        "title": (item.get("title") or url_r.split("/")[-1].replace("-", " "))[:80],
                        "thumb": item.get("thumb", ""),
                    })
                    if len(results) >= 10:
                        break
            except Exception:
                pass
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    # fallback: parse HTML
    if not results and html:
        soup = BeautifulSoup(html, "html.parser")
        seen_bs: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r'/p/\d', href):
                continue
            full_url = href if href.startswith("http") else f"https://www.decathlon.co.ke{href}"
            if full_url in seen_bs:
                continue
            seen_bs.add(full_url)
            title = (a.get("aria-label") or a.get("title") or
                     a.get_text(strip=True)[:80] or
                     full_url.split("/")[-1].replace("-", " "))
            img_tag = a.find("img")
            thumb = (img_tag.get("src") or img_tag.get("data-src") or "") if img_tag else ""
            results.append({"url": full_url, "title": title, "thumb": thumb})
            if len(results) >= 10:
                break
    return results


# ════════════════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════════════════

st.title("🛒 Decathlon Kenya — Image Grabber")
st.caption("Load your Excel file to search & download product images in bulk — no scraping required for products already in the sheet.")

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔧 System Status")
    pw_working, diagnostic_msg = check_playwright_working()
    if pw_working:
        st.success("Playwright operational — JS mode available.")
    else:
        st.error("Playwright not found.")
        st.info(
            "To enable JS mode:\n```bash\npip install playwright\nplaywright install chromium\n```\n"
            f"Diagnostic: `{diagnostic_msg}`"
        )
    st.markdown("---")
    st.markdown("**Tip:** Upload your Excel file to instantly access all product images without scraping.")

# ── Shared settings bar ────────────────────────────────────────────────────
col_n, col_rename, col_pw = st.columns([1, 1, 1])
with col_n:
    max_images = st.slider("Max images per product", 1, 10, 3)
with col_rename:
    rename_by_name = st.toggle("Rename by product name", value=True)
with col_pw:
    use_playwright = st.toggle(
        "JS mode (Playwright)",
        value=pw_working,
        disabled=not pw_working,
        help="Required only for URL-mode scraping when Excel doesn't have images."
    )

# ── Tabs ───────────────────────────────────────────────────────────────────
tab_excel, tab_search, tab_url = st.tabs([
    "📊 Excel Product Search",
    "🔍 Search Decathlon.co.ke",
    "🔗 Bulk Paste URLs / Names"
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Excel Product Search (NEW)
# ════════════════════════════════════════════════════════════════════════════
with tab_excel:
    st.markdown("Upload your Decathlon working file to search products and grab their images directly from the CDN URLs already in the sheet — no browser needed.")

    uploaded = st.file_uploader(
        "Upload Excel file (.xlsx)",
        type=["xlsx"],
        key="excel_upload"
    )

    if uploaded:
        file_bytes = uploaded.read()
        with st.spinner("Loading Excel..."):
            df = load_excel(file_bytes)

        # Stats
        total = len(df)
        exists_count = (df["Status"] == "Exists").sum() if "Status" in df.columns else 0
        missing_count = (df["Status"] == "Does Not Exist").sum() if "Status" in df.columns else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Total rows", f"{total:,}")
        c2.metric("Already on Jumia", f"{exists_count:,}")
        c3.metric("Needs uploading", f"{missing_count:,}")

        st.divider()

        # ── Filter controls ────────────────────────────────────────────────
        col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
        with col_f1:
            search_term = st.text_input(
                "🔎 Search by product name, model label, or brand",
                placeholder="e.g. leggings, running shoes, NH500..."
            )
        with col_f2:
            status_filter = st.selectbox(
                "Status filter",
                ["All", "Does Not Exist", "Exists"],
                index=1
            )
        with col_f3:
            brand_filter = st.selectbox(
                "Brand",
                ["All"] + sorted(df["brand_name"].dropna().unique().tolist())
                if "brand_name" in df.columns else ["All"]
            )

        # Apply filters
        filtered = df.copy()
        if status_filter != "All" and "Status" in filtered.columns:
            filtered = filtered[filtered["Status"] == status_filter]
        if brand_filter != "All" and "brand_name" in filtered.columns:
            filtered = filtered[filtered["brand_name"] == brand_filter]
        if search_term.strip():
            term = search_term.strip().lower()
            mask = (
                filtered["product_name"].fillna("").str.lower().str.contains(term) |
                filtered["model_label"].fillna("").str.lower().str.contains(term) |
                filtered.get("brand_name", pd.Series(dtype=str)).fillna("").str.lower().str.contains(term)
            )
            filtered = filtered[mask]

        # Deduplicate by model_code so we show one row per unique product
        if "model_code" in filtered.columns:
            deduped = filtered.drop_duplicates(subset="model_code", keep="first")
        else:
            deduped = filtered

        st.markdown(f"**{len(deduped):,} unique products** match your filters")

        if len(deduped) == 0:
            st.info("No products match — try adjusting your search or filters.")
        else:
            # ── Bulk download all filtered ─────────────────────────────────
            grab_all = st.button(
                f"⚡ Download ALL {len(deduped):,} products as ZIP",
                type="primary",
                disabled=len(deduped) > 500,
                help="Limited to 500 products at once to avoid timeouts."
            )

            if grab_all:
                progress = st.progress(0.0)
                status_txt = st.empty()
                all_products = []
                for i, (_, row) in enumerate(deduped.iterrows()):
                    slug = extract_slug(str(row.get("product_name", "") or row.get("model_label", "") or "product"))
                    img_urls = get_excel_image_urls(row, max_images)
                    status_txt.markdown(f"Downloading **{i+1}/{len(deduped)}**: {slug}")
                    images = download_all_parallel(img_urls)
                    if images:
                        all_products.append({
                            "slug": slug,
                            "url": "",
                            "images": [(f"{slug}_{j+1:02d}.jpg", to_jpeg_bytes(img), img)
                                       for j, img in enumerate(images)]
                        })
                    progress.progress((i + 1) / len(deduped))

                progress.empty()
                status_txt.empty()

                if all_products:
                    zip_data = make_bulk_zip(all_products)
                    st.download_button(
                        f"📦 Download ZIP ({len(all_products)} products)",
                        data=zip_data,
                        file_name="decathlon_images.zip",
                        mime="application/zip",
                        type="primary"
                    )
                else:
                    st.error("No images could be downloaded.")

            st.divider()

            # ── Product list with individual grab buttons ───────────────────
            st.markdown("**Or grab individual products:**")
            for i, (_, row) in enumerate(deduped.head(50).iterrows()):
                slug = extract_slug(str(row.get("product_name", "") or row.get("model_label", "") or "product"))
                img_urls = get_excel_image_urls(row, max_images)
                brand = row.get("brand_name", "")
                color = row.get("color", "")
                model = row.get("model_code", "")
                status_badge = "🟢" if row.get("Status") == "Exists" else "🔴"

                col_info, col_preview, col_btn = st.columns([4, 2, 1.2])
                with col_info:
                    st.markdown(f"{status_badge} **{row.get('product_name', slug)}**")
                    st.caption(f"Brand: {brand} | Color: {color} | Model: {model} | {len(img_urls)} images available")
                with col_preview:
                    if img_urls:
                        try:
                            st.image(img_urls[0], width=80)
                        except Exception:
                            st.caption("Preview unavailable")
                with col_btn:
                    if st.button("⚡ Grab", key=f"excel_grab_{i}"):
                        with st.spinner("Downloading..."):
                            images = download_all_parallel(img_urls)
                            if images:
                                product = {
                                    "slug": slug,
                                    "url": img_urls[0] if img_urls else "",
                                    "images": [(f"{slug}_{j+1:02d}.jpg", to_jpeg_bytes(img), img)
                                               for j, img in enumerate(images)]
                                }
                                st.session_state["bulk_processed"] = [product]
                                st.success(f"Grabbed {len(images)} image(s)!")
                                st.rerun()
                            else:
                                st.error("Download failed — CDN may be unreachable.")
                st.divider()

            if len(deduped) > 50:
                st.info(f"Showing first 50 of {len(deduped)} products. Use filters or Download ALL to get the rest.")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Search decathlon.co.ke (original)
# ════════════════════════════════════════════════════════════════════════════
with tab_search:
    query = st.text_input(
        "Enter product keyword",
        placeholder="e.g. aquashoes, kids water shoes, running jacket..."
    )
    search_go = st.button("🔍 Search decathlon.co.ke", type="primary",
                          disabled=not query.strip(), key="search_btn")

    if search_go:
        if not pw_working:
            st.error("❌ Search requires Playwright. Install it first (see sidebar).")
        else:
            st.session_state.pop("search_results", None)
            with st.status("Searching Decathlon catalogue...", expanded=False) as status:
                try:
                    results = search_decathlon_kenya(query.strip())
                    st.session_state["search_results"] = results
                    status.update(
                        label=f"Found {len(results)} product(s)" if results else "No products found",
                        state="complete"
                    )
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    status.update(label="Search failed", state="error")

    if "search_results" in st.session_state:
        results = st.session_state["search_results"]
        if not results:
            st.warning("No products found — try different keywords.")
        else:
            st.markdown(f"**{len(results)} result(s):**")
            for i, r in enumerate(results):
                col_thumb, col_info, col_btn = st.columns([1, 5, 1.2])
                with col_thumb:
                    if r.get("thumb"):
                        try:
                            st.image(r["thumb"], width=60)
                        except Exception:
                            st.write("🖼️")
                    else:
                        st.write("🖼️")
                with col_info:
                    st.markdown(f"**{r['title']}**")
                    st.caption(r["url"])
                with col_btn:
                    if st.button("⚡ Grab", key=f"grab_{i}"):
                        with st.spinner("Fetching & downloading images..."):
                            res = run_fetch_url(r["url"], max_images, use_playwright)
                            if res:
                                st.session_state["bulk_processed"] = [res]
                                st.success(f"Grabbed {len(res['images'])} image(s)!")
                                st.rerun()
                            else:
                                st.error("❌ Extraction failed.")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Bulk URL / Name paste (original, improved)
# ════════════════════════════════════════════════════════════════════════════
with tab_url:
    url_input = st.text_area(
        "Paste Decathlon URLs or product names (one per line)",
        placeholder="https://www.decathlon.co.ke/p/8559092-aquashoes-120-blue.html\nWomen's Flared Cotton Fitness Leggings",
        height=180
    )
    url_go = st.button("⚡ Fetch Products", type="primary",
                       disabled=not url_input.strip(), key="url_btn")

    if url_go:
        st.session_state.pop("bulk_processed", None)
        items = [u.strip() for u in url_input.split("\n") if u.strip()]
        progress_bar = st.progress(0.0)
        status_text = st.empty()
        results_list = []
        failures = 0

        for idx, item in enumerate(items):
            target_url = item
            # Resolve non-URLs via DuckDuckGo
            if not re.match(r'https?://', item) or "decathlon.co.ke/p/" not in item:
                status_text.markdown(f"**Resolving:** `{item}`...")
                search_q = f"site:decathlon.co.ke {item}"
                search_url = "https://html.duckduckgo.com/html/?q=" + quote(search_q)
                req = urllib.request.Request(search_url, headers={"User-Agent": HEADERS["User-Agent"]})
                resolved = None
                try:
                    html_ddg = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
                    m = re.search(r'href="(https://www\.decathlon\.co\.ke/p/[^"]+)"', html_ddg)
                    if m:
                        resolved = m.group(1)
                    else:
                        m2 = re.search(r'href="//duckduckgo\.com/l/\?uddg=([^"]+)"', html_ddg)
                        if m2:
                            resolved = unquote(m2.group(1))
                except Exception:
                    pass
                if resolved:
                    target_url = resolved
                    st.info(f"✅ Resolved `{item}` → {target_url}")
                else:
                    st.warning(f"⚠️ Could not resolve: `{item}`")
                    failures += 1
                    progress_bar.progress((idx + 1) / len(items))
                    continue

            status_text.markdown(f"**Processing {idx+1}/{len(items)}:** `{urlparse(target_url).path.split('/')[-1]}`")
            res = run_fetch_url(target_url, max_images, use_playwright)
            if res:
                results_list.append(res)
            else:
                failures += 1
            progress_bar.progress((idx + 1) / len(items))

        progress_bar.empty()
        status_text.empty()
        st.session_state["bulk_processed"] = results_list
        if failures:
            st.warning(f"⚠️ {failures} failure(s) out of {len(items)}.")
        else:
            st.success("✅ Done!")
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# Results display (shared across all tabs)
# ════════════════════════════════════════════════════════════════════════════
if st.session_state.get("bulk_processed"):
    products = st.session_state["bulk_processed"]
    total_imgs = sum(len(p["images"]) for p in products)

    st.divider()
    st.subheader("📦 Results & Downloads", anchor=False)

    if len(products) > 1 or total_imgs > 1:
        zip_data = make_bulk_zip(products)
        st.download_button(
            f"📦 Download Combined ZIP ({total_imgs} images, {len(products)} product(s))",
            data=zip_data,
            file_name="decathlon_bulk_images.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True
        )
        st.markdown("---")

    for p_idx, p in enumerate(products):
        label = p['slug'].replace('_', ' ').replace('-', ' ').title()
        with st.expander(f"🛒 {label} ({len(p['images'])} images)", expanded=True):
            if p.get("url"):
                st.caption(f"Source: {p['url']}")
            cols = st.columns(min(len(p["images"]), 3))
            for idx, (fname, data, pil_img) in enumerate(p["images"]):
                with cols[idx % 3]:
                    st.image(pil_img, caption=f"{fname} ({pil_img.width}×{pil_img.height})")
                    st.download_button(
                        "⬇ JPEG",
                        data=data,
                        file_name=fname,
                        mime="image/jpeg",
                        key=f"dl_{p_idx}_{idx}"
                    )