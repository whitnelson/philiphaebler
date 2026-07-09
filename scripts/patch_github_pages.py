#!/usr/bin/env python3
"""Patch a static site directory for GitHub Pages project-site hosting."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOST_PREFIXES = (
    "assets.squarespace.com",
    "static1.squarespace.com",
    "images.squarespace-cdn.com",
    "definitions.sqspcdn.com",
    "static.squarespace.com",
    "use.typekit.net",
    "p.typekit.net",
)

PROTOCOL_HOST_RE = re.compile(
    r"(?P<quote>['\"]?)(?P<url>(?:https?:)?//(?P<host>"
    + "|".join(re.escape(h) for h in HOST_PREFIXES)
    + r")(?P<path>/[^\"'\\s<>)]*))",
    re.IGNORECASE,
)

RUNTIME_PATCH = """<script data-github-pages-base="">(function(){var m=location.pathname.match(/^\\/([^/]+)\\//);var base=(location.hostname.endsWith('.github.io')&&m)?'/'+m[1]:'';if(!base)return;var maps=[['https://assets.squarespace.com',base+'/_assets/assets.squarespace.com'],['http://assets.squarespace.com',base+'/_assets/assets.squarespace.com'],['//assets.squarespace.com',base+'/_assets/assets.squarespace.com'],['https://static1.squarespace.com',base+'/_assets/static1.squarespace.com'],['http://static1.squarespace.com',base+'/_assets/static1.squarespace.com'],['//static1.squarespace.com',base+'/_assets/static1.squarespace.com'],['https://images.squarespace-cdn.com',base+'/_assets/images.squarespace-cdn.com'],['http://images.squarespace-cdn.com',base+'/_assets/images.squarespace-cdn.com'],['//images.squarespace-cdn.com',base+'/_assets/images.squarespace-cdn.com'],['https://definitions.sqspcdn.com',base+'/_assets/definitions.sqspcdn.com'],['http://definitions.sqspcdn.com',base+'/_assets/definitions.sqspcdn.com'],['//definitions.sqspcdn.com',base+'/_assets/definitions.sqspcdn.com']];function rw(u){if(!u||typeof u!=='string')return u;for(var i=0;i<maps.length;i++){if(u.indexOf(maps[i][0])===0)return maps[i][1]+u.slice(maps[i][0].length);}return u;}if(window.fetch){var f=window.fetch.bind(window);window.fetch=function(i,n){if(typeof i==='string')i=rw(i);else if(i&&i.url){try{i=new Request(rw(i.url),i);}catch(e){}}return f(i,n);};}var o=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=rw(u);return o.apply(this,arguments);};var c=document.createElement.bind(document);document.createElement=function(t){var el=c(t);if(t&&(t.toLowerCase()==='script'||t.toLowerCase()==='link')){var s=el.setAttribute.bind(el);el.setAttribute=function(n,v){if((n==='src'||n==='href')&&v)v=rw(v);return s(n,v);};}return el;};})();</script>"""


def rel_path_from(source_file: Path, target_file: Path) -> str:
    rel = os.path.relpath(target_file, source_file.parent)
    return rel.replace(os.sep, "/")


def local_asset_path(site_dir: Path, host: str, path: str) -> Path | None:
    candidate = site_dir / "_assets" / host / path.lstrip("/")
    if candidate.exists():
        return candidate
    return None


def rewrite_protocol_urls(text: str, source_file: Path, site_dir: Path) -> str:
    def replace(match: re.Match) -> str:
        host = match.group("host")
        path = match.group("path")
        local = local_asset_path(site_dir, host, path)
        if not local:
            return match.group(0)
        rel = rel_path_from(source_file, local)
        return f"{match.group('quote')}{rel}"

    return PROTOCOL_HOST_RE.sub(replace, text)


def patch_typekit_js(text: str, base: str) -> str:
    base = base.rstrip("/")
    if base:
        if f'"{base}/indexaf/' not in text:
            text = text.replace('"/indexaf/', f'"{base}/indexaf/')
            text = text.replace("'/indexaf/", f"'{base}/indexaf/")
        if f'"{base}/p.typekit.net/' not in text:
            text = text.replace('"/p.typekit.net/', f'"{base}/p.typekit.net/')
            text = text.replace("'/p.typekit.net/", f"'{base}/p.typekit.net/")
    text = text.replace(
        "if(this.j&&(a=location.hostname,!this.j.has(a)))",
        "if(false&&(a=location.hostname,!this.j.has(a)))",
    )
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


def inject_runtime_patch(text: str) -> str:
    if 'data-github-pages-base=""' in text:
        return text
    marker = "<head>"
    if marker not in text:
        return text
    return text.replace(marker, marker + RUNTIME_PATCH, 1)


def patch_tree(site_dir: Path, base: str) -> int:
    changed = 0
    for path in site_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".nojekyll":
            continue
        if path.suffix not in {".html", ".js", ".css", ".svg"}:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        new = rewrite_protocol_urls(text, path, site_dir)
        if path.suffix == ".html":
            new = patch_internal_links(new, base)
            new = inject_runtime_patch(new)
        if "use.typekit.net/ik/" in str(path) and path.suffix == ".js":
            new = patch_typekit_js(new, base)

        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
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
