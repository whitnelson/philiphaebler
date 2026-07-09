#!/usr/bin/env python3
"""
Archive philiphaebler.com Squarespace site as a fully offline static mirror.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, unquote, quote

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.philiphaebler.com"
SITE_HOSTS = {
    "www.philiphaebler.com",
    "philiphaebler.com",
}
ASSET_HOSTS = SITE_HOSTS | {
    "assets.squarespace.com",
    "static1.squarespace.com",
    "images.squarespace-cdn.com",
    "use.typekit.net",
    "p.typekit.net",
    "definitions.sqspcdn.com",
    "static.squarespace.com",
}
SKIP_PATH_PREFIXES = ("/api/", "/universal/scripts/")
EXTERNAL_LINK_HOSTS = {"philiphaebler.smugmug.com", "squarespace.com", "www.squarespace.com"}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "site"
MANIFEST_PATH = ROOT / "manifest.json"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
)

URL_RE = re.compile(
    r"""(?P<quote>["'])"""
    r"""(?P<url>"""
    r"""(?:https?:)?//[^"'()\s]+|"""
    r"""/[^"'()\s]*"""
    r""")"""
    r"""(?P=quote)""",
    re.VERBOSE,
)

CSS_URL_RE = re.compile(r"url\(\s*(?P<quote>['\"]?)(?P<url>[^)'\"]+)(?P=quote)\s*\)")


def normalize_url(url: str, base: str | None = None) -> str | None:
    if not url or url.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:", "#")):
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.netloc.lower()
    if host in SITE_HOSTS:
        host = "www.philiphaebler.com"
        scheme = "https"
    else:
        scheme = parsed.scheme
    path = parsed.path or "/"
    if host in SITE_HOSTS and (" " in path or "(" in path):
        return None
    return urlunparse((scheme, host, path, "", parsed.query, ""))


def is_site_page(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in SITE_HOSTS:
        return False
    if any(parsed.path.startswith(p) for p in SKIP_PATH_PREFIXES):
        return False
    if parsed.path.startswith("/api/"):
        return False
    if parsed.path.startswith("/block-test"):
        return False
    return True


def is_asset_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() in ASSET_HOSTS


def url_to_local_path(url: str, is_page: bool = False) -> Path:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = unquote(parsed.path)
    query = parsed.query

    if host in SITE_HOSTS:
        if path in ("", "/"):
            rel = "index.html"
        elif path.endswith("/"):
            rel = path.lstrip("/") + "index.html"
        elif is_page and not Path(path).suffix:
            rel = path.lstrip("/") + "/index.html"
        else:
            rel = path.lstrip("/")
        if query:
            rel = rel + "?" + query
        return OUTPUT_DIR / rel

    # External assets under _assets/<host>/...
    rel_path = path.lstrip("/")
    if not rel_path:
        rel_path = "index"
    # Hash very long filenames (typekit kit JS etc.)
    stem = Path(rel_path)
    if len(stem.name) > 120:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        ext = stem.suffix or ".bin"
        rel_path = str(stem.parent / f"{digest}{ext}") if str(stem.parent) != "." else f"{digest}{ext}"
    if query:
        safe_q = hashlib.md5(query.encode()).hexdigest()[:8]
        stem = Path(rel_path)
        if stem.suffix:
            rel_path = str(stem.with_suffix(stem.suffix + f".{safe_q}"))
        else:
            rel_path = f"{rel_path}.{safe_q}"
    return OUTPUT_DIR / "_assets" / host / rel_path


def ensure_parent_dir(path: Path) -> None:
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        parent.unlink()
    parent.mkdir(parents=True, exist_ok=True)


def rel_path_from(source_file: Path, target_file: Path) -> str:
    rel = os.path.relpath(target_file, source_file.parent)
    return rel.replace(os.sep, "/")


def fetch_bytes(url: str) -> tuple[bytes, str]:
    for attempt in range(4):
        try:
            resp = SESSION.get(url, timeout=60)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
            return resp.content, ctype
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
            last_exc = exc
    raise last_exc  # pragma: no cover


def discover_sitemap_images() -> set[str]:
    urls: set[str] = set()
    try:
        xml = SESSION.get(BASE_URL + "/sitemap.xml", timeout=30).text
        for m in re.finditer(r"<image:loc>([^<]+)</image:loc>", xml):
            u = normalize_url(m.group(1))
            if u:
                urls.add(u)
    except Exception as exc:
        print(f"Warning: sitemap images failed: {exc}")
    return urls


def discover_pages() -> list[str]:
    pages: set[str] = set()
    for p in ["/", "/portfolio-LVW8P", "/travel", "/iceland", "/mongolia", "/cuba",
              "/patagonia", "/palouse", "/myanmar", "/blog", "/about", "/contact"]:
        u = normalize_url(urljoin(BASE_URL, p))
        if u:
            pages.add(u)

    try:
        xml = SESSION.get(BASE_URL + "/sitemap.xml", timeout=30).text
        root = ET.fromstring(xml)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            if loc.text:
                u = normalize_url(loc.text.replace("http://", "https://"))
                if u and is_site_page(u):
                    pages.add(u)
    except Exception as exc:
        print(f"Warning: sitemap parse failed: {exc}")

    # Crawl blog index for post links
    try:
        blog_html = SESSION.get(BASE_URL + "/blog", timeout=30).text
        soup = BeautifulSoup(blog_html, "lxml")
        for a in soup.find_all("a", href=True):
            u = normalize_url(a["href"], BASE_URL + "/blog")
            if u and is_site_page(u) and "/blog/" in u:
                pages.add(u)
    except Exception as exc:
        print(f"Warning: blog crawl failed: {exc}")

    return sorted(pages)


def extract_urls_from_text(text: str, base: str) -> set[str]:
    found: set[str] = set()
    for m in URL_RE.finditer(text):
        u = normalize_url(m.group("url"), base)
        if u:
            found.add(u)
    for m in CSS_URL_RE.finditer(text):
        u = normalize_url(m.group("url"), base)
        if u:
            found.add(u)
    return found


def extract_urls_from_html(html: str, base: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for tag, attr in [
        ("a", "href"),
        ("link", "href"),
        ("script", "src"),
        ("img", "src"),
        ("img", "data-src"),
        ("source", "src"),
        ("video", "src"),
        ("audio", "src"),
        ("iframe", "src"),
        ("use", "href"),
        ("use", "xlink:href"),
    ]:
        for el in soup.find_all(tag):
            val = el.get(attr)
            if not val:
                continue
            u = normalize_url(val, base)
            if u:
                found.add(u)

    # Inline styles only — avoid scanning huge JSON config blobs in script tags
    for el in soup.find_all(style=True):
        found |= extract_urls_from_text(el["style"], base)

    # Image URLs embedded in noscript / data attributes
    for el in soup.find_all(True):
        for attr_name, val in el.attrs.items():
            if attr_name in ("style", "class", "id"):
                continue
            if isinstance(val, str) and ("squarespace" in val or val.startswith("/")):
                u = normalize_url(val, base)
                if u and (is_asset_url(u) or is_site_page(u)):
                    found.add(u)

    return found


def is_downloadable_asset(url: str) -> bool:
    if not is_asset_url(url):
        return False
    parsed = urlparse(url)
    path = parsed.path
    # Skip typekit SPA template placeholders scraped from minified JS
    if parsed.netloc.lower() in ("use.typekit.net", "p.typekit.net"):
        if any(x in path for x in ("angular_templates", "{", "}", "\\u0026")):
            return False
        if path in ("/fonts", "/search", "/about", "/help", "/discovery", "/called"):
            return False
    return True


def discover_typekit_assets(typekit_js_url: str) -> set[str]:
    urls: set[str] = set()
    try:
        js, _ = fetch_bytes(typekit_js_url)
        text = js.decode("utf-8", errors="replace")
        # Only follow CSS + font file references
        for m in re.finditer(
            r"https?://p\.typekit\.net/[a-zA-Z0-9_.-]+\.css[^\"'\s]*", text
        ):
            u = normalize_url(m.group(0))
            if u:
                urls.add(u)
        for m in re.finditer(
            r"https?://use\.typekit\.net/[a-zA-Z0-9_./-]+\.(?:woff2?|otf|ttf)[^\"'\s]*",
            text,
        ):
            u = normalize_url(m.group(0))
            if u:
                urls.add(u)
    except Exception as exc:
        print(f"Warning: typekit discovery failed for {typekit_js_url}: {exc}")
    return urls


def download_asset(url: str, manifest: dict) -> Path | None:
    if is_site_page(url):
        manifest["pending_pages"].add(url)
        return None
    if not is_downloadable_asset(url):
        return None
    if url in manifest["assets"]:
        return Path(manifest["assets"][url])

    local = url_to_local_path(url, is_page=False)
    if local.exists() and local.stat().st_size > 0:
        manifest["assets"][url] = str(local.relative_to(OUTPUT_DIR))
        return local

    try:
        data, ctype = fetch_bytes(url)
    except Exception as exc:
        print(f"  FAIL asset: {url} ({exc})")
        manifest["failed"].append(url)
        return None

    try:
        ensure_parent_dir(local)
        local.write_bytes(data)
    except OSError as exc:
        print(f"  FAIL write: {url} ({exc})")
        manifest["failed"].append(url)
        return None
    manifest["assets"][url] = str(local.relative_to(OUTPUT_DIR))

    # Recurse into CSS for nested fonts/images
    if local.suffix == ".css":
        try:
            text = data.decode("utf-8", errors="replace")
            nested = extract_urls_from_text(text, url)
            for n in nested:
                if is_downloadable_asset(n):
                    download_asset(n, manifest)
        except Exception:
            pass
    return local


def rewrite_text_content(text: str, source_url: str, source_file: Path, manifest: dict) -> str:
    base = source_url

    def replace_url(match: re.Match) -> str:
        raw = match.group("url")
        u = normalize_url(raw, base)
        if not u:
            return match.group(0)
        if u in manifest["assets"]:
            target = OUTPUT_DIR / manifest["assets"][u]
            return match.group("quote") + rel_path_from(source_file, target) + match.group("quote")
        if u in manifest["pages"]:
            target = OUTPUT_DIR / manifest["pages"][u]
            return match.group("quote") + rel_path_from(source_file, target) + match.group("quote")
        parsed = urlparse(u)
        if parsed.netloc.lower() in SITE_HOSTS:
            target = url_to_local_path(u, is_page=True)
            if target.exists():
                return match.group("quote") + rel_path_from(source_file, target) + match.group("quote")
        return match.group(0)

    text = URL_RE.sub(replace_url, text)

    def replace_css_url(match: re.Match) -> str:
        raw = match.group("url").strip()
        u = normalize_url(raw, base)
        if not u:
            return match.group(0)
        if u in manifest["assets"]:
            target = OUTPUT_DIR / manifest["assets"][u]
            new = rel_path_from(source_file, target)
            q = match.group("quote") or ""
            return f"url({q}{new}{q})"
        return match.group(0)

    text = CSS_URL_RE.sub(replace_css_url, text)
    return text


def rewrite_all_external_urls(text: str, source_file: Path, manifest: dict) -> str:
    """Replace every known archived asset URL variant anywhere in the document."""
    replacements: list[tuple[str, str]] = []
    for url, rel in manifest["assets"].items():
        local = rel_path_from(source_file, OUTPUT_DIR / rel)
        replacements.append((url, local))
        parsed = urlparse(url)
        if parsed.scheme == "https":
            replacements.append((f"http://{parsed.netloc}{parsed.path}", local))
            if parsed.query:
                replacements.append((f"http://{parsed.netloc}{parsed.path}?{parsed.query}", local))
            replacements.append((f"//{parsed.netloc}{parsed.path}", local))
            if parsed.query:
                replacements.append((f"//{parsed.netloc}{parsed.path}?{parsed.query}", local))

    for src, dst in sorted(replacements, key=lambda x: -len(x[0])):
        text = text.replace(src, dst)
    return text


OFFLINE_PATCH = """<script data-archive-offline>(function(){var ok=function(){return Promise.resolve(new Response('{}',{status:200,headers:{'Content-Type':'application/json'}}));};if(window.fetch){var f=window.fetch.bind(window);window.fetch=function(i,n){var u=typeof i==='string'?i:(i&&i.url?i.url:'');if(u&&(/\\/api\\//.test(u)||u.indexOf('squarespace.com')!==-1&&n&&n.method==='POST'))return ok();return f(i,n);};}var o=XMLHttpRequest.prototype.open, s=XMLHttpRequest.prototype.send;XMLHttpRequest.prototype.open=function(m,u){this._archiveOffline=m==='POST'&&u&&/\\/api\\//.test(u);return o.apply(this,arguments);};XMLHttpRequest.prototype.send=function(){if(this._archiveOffline){Object.defineProperty(this,'status',{value:200});Object.defineProperty(this,'readyState',{value:4});this.responseText='{}';this.dispatchEvent(new Event('load'));return;}return s.apply(this,arguments);};})();</script>"""


def patch_html_for_offline(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # Remove preconnect to external hosts (avoid console noise)
    for link in soup.find_all("link", rel="preconnect"):
        href = link.get("href", "")
        if href and urlparse(href).netloc.lower() not in SITE_HOSTS:
            link.decompose()

    # Remove Squarespace error reporter (POSTs to external API)
    for script in soup.find_all("script"):
        src = script.get("src", "")
        if "visitor-site-error-reporter" in src:
            script.decompose()
            continue
        text = script.string or script.get_text() or ""
        if "squarespace-visitor_site_error_reporter" in text:
            script.decompose()

    # Neutralize Typekit load errors while keeping fonts
    for script in soup.find_all("script"):
        onload = script.get("onload")
        if onload and "Typekit.load" in onload:
            script["onload"] = (
                "try{Typekit.load();}catch(e){} document.documentElement.classList.remove('wf-loading');"
            )

    # Remove canonical/og pointing off-site duplicates — keep content
    head = soup.find("head")
    if head and not soup.find("script", attrs={"data-archive-offline": True}):
        patch = BeautifulSoup(OFFLINE_PATCH, "lxml")
        head.insert(0, patch.script)

    return str(soup)


def download_page(url: str, manifest: dict) -> None:
    print(f"Page: {url}", flush=True)
    try:
        resp = SESSION.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        final_url = normalize_url(resp.url) or url
        html = resp.text
    except Exception as exc:
        print(f"  FAIL page: {url} ({exc})")
        manifest["failed"].append(url)
        return

    local = url_to_local_path(final_url, is_page=True)
    manifest["pages"][final_url] = str(local.relative_to(OUTPUT_DIR))
    if final_url != url:
        manifest["redirects"][url] = final_url

    asset_urls = extract_urls_from_html(html, final_url)
    # Also pull script/link tags from raw HTML for protocol-relative URLs
    for m in re.finditer(r'<(?:script|link)[^>]+(?:src|href)=["\']([^"\']+)["\']', html, re.I):
        u = normalize_url(m.group(1), final_url)
        if u:
            asset_urls.add(u)

    for a in sorted(asset_urls):
        if is_downloadable_asset(a):
            download_asset(a, manifest)
            if "use.typekit.net/ik/" in a:
                for tk in discover_typekit_assets(a):
                    download_asset(tk, manifest)
        elif is_site_page(a):
            manifest["pending_pages"].add(a)

    html = rewrite_text_content(html, final_url, local, manifest)
    html = rewrite_all_external_urls(html, local, manifest)
    html = patch_html_for_offline(html)

    ensure_parent_dir(local)
    if local.exists() and local.is_dir():
        shutil.rmtree(local)
    local.write_text(html, encoding="utf-8")


def rewrite_downloaded_assets(manifest: dict) -> None:
    for url, rel in list(manifest["assets"].items()):
        path = OUTPUT_DIR / rel
        if not path.exists():
            continue
        if path.suffix not in (".css", ".js", ".svg", ".html"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = rewrite_text_content(text, url, path, manifest)
        new = rewrite_all_external_urls(new, path, manifest)
        if new != text:
            path.write_text(new, encoding="utf-8")

    for page_url, rel in manifest["pages"].items():
        path = OUTPUT_DIR / rel
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = rewrite_all_external_urls(text, path, manifest)
        new = patch_html_for_offline(new) if "<html" in new else new
        if new != text:
            path.write_text(new, encoding="utf-8")


def download_typekit_fonts(manifest: dict) -> None:
    """Download Adobe Typekit font files to /indexaf/ paths expected by local kit JS."""
    print("Downloading Typekit fonts...", flush=True)
    font_urls: set[str] = set()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return

    pages = sorted(manifest["pages"].keys()) or [BASE_URL + "/"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        for page_url in pages:
            page = context.new_page()
            def capture(req):
                u = req.url
                if "use.typekit.net/af/" in u or "use.typekit.net/indexaf/" in u:
                    font_urls.add(u.split("?")[0])
            page.on("request", capture)
            try:
                page.goto(page_url, wait_until="networkidle", timeout=120000)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            page.close()
        browser.close()

    for url in sorted(font_urls):
        parsed = urlparse(url)
        path = parsed.path
        if path.startswith("/af/"):
            path = "/indexaf/" + path[len("/af/") :]
        elif not path.startswith("/indexaf/"):
            continue
        for sub_url in {url, url.replace("/31/l", "/31/d"), url.replace("/31/l", "/31/a")}:
            sub_path = urlparse(sub_url).path
            if sub_path.startswith("/af/"):
                sub_path = "/indexaf/" + sub_path[len("/af/") :]
            local = OUTPUT_DIR / sub_path.lstrip("/")
            if local.exists() and local.stat().st_size > 0:
                continue
            try:
                data, _ = fetch_bytes(sub_url.split("?")[0])
                ensure_parent_dir(local)
                local.write_bytes(data)
                manifest["assets"][sub_url.split("?")[0]] = str(local.relative_to(OUTPUT_DIR))
                print(f"  font: {sub_path}", flush=True)
            except Exception as exc:
                print(f"  FAIL font: {sub_url} ({exc})", flush=True)


def fix_typekit_paths() -> None:
    """Ensure Typekit kit JS uses root-absolute font paths."""
    ik_dir = OUTPUT_DIR / "_assets" / "use.typekit.net" / "ik"
    if not ik_dir.exists():
        return
    for js in ik_dir.glob("*.js"):
        text = js.read_text(encoding="utf-8", errors="replace")
        new = (
            text.replace('"../indexaf/', '"/indexaf/')
            .replace("'../indexaf/", "'/indexaf/")
            .replace("../../p.typekit.net/", "/p.typekit.net/")
            .replace('"../p.typekit.net/', '"/p.typekit.net/')
        )
        if new != text:
            js.write_text(new, encoding="utf-8")


def create_typekit_stubs() -> None:
    stub_dir = OUTPUT_DIR / "p.typekit.net"
    stub_dir.mkdir(parents=True, exist_ok=True)
    gif = stub_dir / "p.gif"
    if not gif.exists():
        try:
            gif.write_bytes(fetch_bytes("https://p.typekit.net/p.gif")[0])
        except Exception:
            gif.write_bytes(
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            )
    (stub_dir / "indexp.gif").write_bytes(gif.read_bytes())
    ht = OUTPUT_DIR / ".htaccess"
    ht.write_text(
        """# Squarespace static archive
DirectoryIndex index.html

<IfModule mod_rewrite.c>
  RewriteEngine On
  RewriteBase /

  # Serve directory indexes
  RewriteCond %{REQUEST_FILENAME} -d
  RewriteCond %{REQUEST_FILENAME}/index.html -f
  RewriteRule ^(.+)$ $1/index.html [L]
</IfModule>
""",
        encoding="utf-8",
    )


def run_playwright_discovery(pages: list[str], manifest: dict) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not available; skipping browser discovery")
        return

    print("\nBrowser asset discovery...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        seen_requests: set[str] = set()

        def handle_response(response):
            try:
                u = normalize_url(response.url)
                if not u or u in seen_requests:
                    return
                if response.status >= 400:
                    return
                seen_requests.add(u)
                if is_downloadable_asset(u):
                    download_asset(u, manifest)
            except Exception:
                pass

        for page_url in pages:
            page = context.new_page()
            page.on("response", handle_response)
            try:
                print(f"  render: {page_url}")
                page.goto(page_url, wait_until="networkidle", timeout=120000)
                page.wait_for_timeout(2000)
                # Scroll to trigger lazy images
                page.evaluate(
                    """async () => {
                      const delay = ms => new Promise(r => setTimeout(r, ms));
                      for (let y = 0; y < document.body.scrollHeight; y += 400) {
                        window.scrollTo(0, y);
                        await delay(100);
                      }
                      window.scrollTo(0, 0);
                    }"""
                )
                page.wait_for_timeout(1500)
                html = page.content()
                for u in extract_urls_from_html(html, page_url):
                    if is_asset_url(u):
                        download_asset(u, manifest)
                    elif is_site_page(u):
                        manifest["pending_pages"].add(u)
            except Exception as exc:
                print(f"  WARN render {page_url}: {exc}")
            finally:
                page.close()
        browser.close()


def main() -> int:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    manifest = {
        "base_url": BASE_URL,
        "pages": {},
        "assets": {},
        "redirects": {},
        "pending_pages": set(),
        "failed": [],
    }

    seed_pages = discover_pages()
    print(f"Discovered {len(seed_pages)} seed pages", flush=True)

    sitemap_images = discover_sitemap_images()
    print(f"Discovered {len(sitemap_images)} sitemap images", flush=True)
    for img_url in sitemap_images:
        download_asset(img_url, manifest)

    queue: deque[str] = deque(seed_pages)
    visited: set[str] = set()

    while queue:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        download_page(url, manifest)
        while manifest["pending_pages"]:
            pending = manifest["pending_pages"].pop()
            if pending not in visited:
                queue.append(pending)

    run_playwright_discovery(sorted(manifest["pages"].keys()) or seed_pages, manifest)

    # Second pass for any pages found during browser discovery
    while manifest["pending_pages"]:
        pending = manifest["pending_pages"].pop()
        if pending not in visited:
            visited.add(pending)
            download_page(pending, manifest)

    print("\nRewriting asset references...")
    rewrite_downloaded_assets(manifest)
    fix_typekit_paths()
    download_typekit_fonts(manifest)

    # Convert sets for JSON
    out_manifest = {
        k: (sorted(v) if isinstance(v, set) else v)
        for k, v in manifest.items()
    }
    MANIFEST_PATH.write_text(json.dumps(out_manifest, indent=2), encoding="utf-8")
    create_typekit_stubs()

    print(f"\nDone. Pages: {len(manifest['pages'])}, Assets: {len(manifest['assets'])}")
    if manifest["failed"]:
        print(f"Failures: {len(manifest['failed'])}")
        for f in manifest["failed"][:20]:
            print(f"  - {f}")
    print(f"Output: {OUTPUT_DIR}")
    return 0 if not manifest["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
