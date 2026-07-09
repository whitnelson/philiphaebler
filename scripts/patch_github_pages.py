#!/usr/bin/env python3
"""Patch a static site directory for GitHub Pages project-site hosting."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Neutral directory names avoid ad blockers that match "squarespace" in URLs.
HOST_MAP = {
    "assets.squarespace.com": "asq",
    "static1.squarespace.com": "st1",
    "images.squarespace-cdn.com": "img",
    "definitions.sqspcdn.com": "def",
    "static.squarespace.com": "stq",
    "use.typekit.net": "tk",
    "p.typekit.net": "pkt",
}

TEXT_SUFFIXES = {".html", ".js", ".css", ".svg", ".json"}

EXTRACT_CSS = (
    "35ebb88013744865-min.en-US.css",
    "8308d1793f387442-min.en-US.css",
)

BOOTSTRAP_PATCH = (
    '<script data-archive-bootstrap="">(function(){function boot(){'
    "document.querySelectorAll('img[data-src]').forEach(function(i){"
    "if(!i.getAttribute('src'))i.setAttribute('src',i.getAttribute('data-src'));});"
    "document.documentElement.classList.remove('wf-loading');}"
    "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);"
    "else boot();})();</script>"
)


def build_runtime_patch(base: str) -> str:
    pairs = []
    for host, short in HOST_MAP.items():
        target = f"{base}/_assets/{short}"
        for prefix in ("https:", "http:", ""):
            src = f"{prefix}//{host}" if prefix else f"//{host}"
            pairs.append(f"['{src}','{target}']")
    maps = ",".join(pairs)
    return (
        '<script data-github-pages-base="">(function(){var m=location.pathname.match(/^\\/([^/]+)\\//);'
        "var base=(location.hostname.endsWith('.github.io')&&m)?'/'+m[1]:'';if(!base)return;"
        f"var maps=[{maps}];"
        "function rw(u){if(!u||typeof u!=='string')return u;for(var i=0;i<maps.length;i++){"
        "if(u.indexOf(maps[i][0])===0)return maps[i][1]+u.slice(maps[i][0].length);}return u;}"
        "if(window.fetch){var f=window.fetch.bind(window);window.fetch=function(i,n){"
        "if(typeof i==='string')i=rw(i);else if(i&&i.url){try{i=new Request(rw(i.url),i);}catch(e){}}"
        "return f(i,n);};}"
        "var o=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){"
        "arguments[1]=rw(u);return o.apply(this,arguments);};"
        "var c=document.createElement.bind(document);document.createElement=function(t){"
        "var el=c(t);if(t&&(t.toLowerCase()==='script'||t.toLowerCase()==='link')){"
        "var s=el.setAttribute.bind(el);el.setAttribute=function(n,v){"
        "if((n==='src'||n==='href')&&v)v=rw(v);return s(n,v);};}return el;};})();</script>"
    )


def rename_asset_dirs(site_dir: Path) -> None:
    assets_root = site_dir / "_assets"
    if not assets_root.is_dir():
        return
    for old_host, short in HOST_MAP.items():
        old_dir = assets_root / old_host
        new_dir = assets_root / short
        if old_dir.is_dir() and not new_dir.exists():
            old_dir.rename(new_dir)


def asset_url(base: str, short: str) -> str:
    return f"{base.rstrip('/')}/_assets/{short}"


def rewrite_asset_paths(text: str, base: str) -> str:
    for host, short in HOST_MAP.items():
        target = asset_url(base, short)
        text = re.sub(
            rf"(?:\.\./)*_assets/{re.escape(host)}",
            target,
            text,
        )
        text = re.sub(
            rf"https?://{re.escape(host)}",
            target,
            text,
        )

    text = re.sub(r"<base href=\"\"\s*/?>", "", text)
    return text


def rewrite_all_text_files(site_dir: Path, base: str) -> int:
    changed = 0
    for path in site_dir.rglob("*"):
        if not path.is_file() or path.name == ".nojekyll":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if not any(token in text for token in ("squarespace", "sqspcdn", "typekit.net", "_assets/")):
            continue
        new = rewrite_asset_paths(text, base)
        if path.suffix == ".html":
            new = patch_html_delivery(new, base)
            new = inject_runtime_patch(new, base)
        if "/_assets/tk/" in str(path) and path.suffix == ".js":
            new = patch_typekit_js(new, base)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def fix_asset_extensions(site_dir: Path, base: str) -> None:
    """Give key assets proper file extensions so GitHub Pages serves correct MIME types."""
    for hashed_css in site_dir.rglob("site.css.*"):
        if hashed_css.name == "site.css":
            continue
        plain = hashed_css.with_name("site.css")
        if not plain.exists():
            plain.write_bytes(hashed_css.read_bytes())

    for combo in site_dir.rglob("combo/.baa6bf59"):
        js_combo = combo.with_name(".baa6bf59.js")
        if not js_combo.exists():
            js_combo.write_bytes(combo.read_bytes())


def patch_typekit_js(text: str, base: str) -> str:
    base = base.rstrip("/")
    if base:
        if f'"{base}/indexaf/' not in text:
            text = text.replace('"/indexaf/', f'"{base}/indexaf/')
            text = text.replace("'/indexaf/", f"'{base}/indexaf/")
        pkt = f"{base}/pkt"
        if f'"{pkt}/' not in text:
            text = text.replace('"/p.typekit.net/', f'"{pkt}/')
            text = text.replace("'/p.typekit.net/", f"'{pkt}/")
        text = text.replace('"/p.typekit.net/', f'"{pkt}/')
        text = text.replace('"ping":"/p.typekit.net/', f'"ping":"{pkt}/')
    text = text.replace(
        "if(this.j&&(a=location.hostname,!this.j.has(a)))",
        "if(false&&(a=location.hostname,!this.j.has(a)))",
    )
    return text


def patch_html_delivery(text: str, base: str) -> str:
    base = base.rstrip("/")
    text = re.sub(r'\s+crossorigin="anonymous"', "", text)
    text = text.replace("site.css.ce97a3fe", "site.css")
    text = text.replace("/combo/.baa6bf59", "/combo/.baa6bf59.js")

    def add_img_src(match: re.Match) -> str:
        tag = match.group(0)
        if ' src="' in tag or " src='" in tag:
            return tag
        data_src = re.search(r'data-src="([^"]+)"', tag)
        if not data_src:
            return tag
        return tag[:-1] + f' src="{data_src.group(1)}">'

    text = re.sub(r"<img\b[^>]*data-src=\"[^\"]+\"[^>]*/?>", add_img_src, text)

    extra_css = []
    for name in EXTRACT_CSS:
        path = f"{base}/_assets/asq/universal/styles-compressed/{name}"
        if f'href="{path}"' not in text:
            extra_css.append(
                f'<link href="{path}" rel="stylesheet" type="text/css"/>'
            )
    if extra_css and "</head>" in text:
        text = text.replace("</head>", "".join(extra_css) + "</head>", 1)

    if 'data-archive-bootstrap=""' not in text:
        text = text.replace("</body>", BOOTSTRAP_PATCH + "</body>", 1)

    return text


def patch_internal_links(text: str, base: str) -> str:
    base = base.rstrip("/")
    if not base:
        return text

    def replace_href(match: re.Match) -> str:
        path = match.group(1)
        if path.startswith(base + "/") or path == base:
            return match.group(0)
        return f'href="{base}{path}"'

    return re.sub(r'href="(/(?!/)[^"]*)"', replace_href, text)


def inject_runtime_patch(text: str, base: str) -> str:
    patch = build_runtime_patch(base.rstrip("/"))
    if 'data-github-pages-base=""' in text:
        text = re.sub(
            r'<script data-github-pages-base="">.*?</script>',
            patch,
            text,
            count=1,
            flags=re.DOTALL,
        )
        return text
    marker = "<head>"
    if marker not in text:
        return text
    return text.replace(marker, marker + patch, 1)


def patch_tree(site_dir: Path, base: str) -> int:
    rename_asset_dirs(site_dir)
    pkt_root = site_dir / "p.typekit.net"
    pkt_target = site_dir / "pkt"
    if pkt_root.is_dir() and not pkt_target.exists():
        pkt_root.rename(pkt_target)

    fix_asset_extensions(site_dir, base)

    changed = 0
    for path in site_dir.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        new = patch_internal_links(text, base)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1

    changed += rewrite_all_text_files(site_dir, base)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=ROOT / "docs",
        help="Site directory to patch (default: docs/)",
    )
    parser.add_argument(
        "--base",
        default="/philiphaebler",
        help="GitHub Pages project path prefix (default: /philiphaebler)",
    )
    args = parser.parse_args()

    site_dir = args.dir.resolve()
    if not site_dir.is_dir():
        print(f"Directory not found: {site_dir}")
        return 1

    (site_dir / ".nojekyll").touch()
    changed = patch_tree(site_dir, args.base)
    print(f"Patched {changed} files in {site_dir} (base={args.base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
