#!/usr/bin/env python3
"""Test archived static site for console errors and broken resources."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
MANIFEST_PATH = ROOT / "manifest.json"
REPORT_PATH = ROOT / "test-report.json"


def collect_pages() -> list[str]:
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text())
        pages = manifest.get("pages", {})
        # Prefer canonical https URLs only
        canonical = [
            u
            for u in pages
            if u.startswith("https://www.philiphaebler.com")
            and "/block-test" not in u
        ]
        return sorted(canonical or pages.keys())
    index = SITE_DIR / "index.html"
    if index.exists():
        return ["https://www.philiphaebler.com/"]
    return []


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install playwright: pip install playwright && playwright install chromium")
        return 1

    if not SITE_DIR.exists():
        print(f"Site directory not found: {SITE_DIR}")
        return 1

    pages = collect_pages()
    if not pages:
        print("No pages found to test")
        return 1

    report = {"pages": {}, "summary": {"errors": 0, "warnings": 0, "failed_requests": 0}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)

        for page_url in pages:
            manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {"pages": {}}
            rel = manifest.get("pages", {}).get(page_url, "index.html")
            local_url = urljoin("http://localhost:8765/", rel)

            page = context.new_page()
            page_errors: list[str] = []
            console_issues: list[dict] = []
            failed_requests: list[dict] = []

            page.on("pageerror", lambda err, pe=page_errors: pe.append(str(err)))
            page.on(
                "console",
                lambda msg, ci=console_issues: ci.append(
                    {"type": msg.type, "text": msg.text}
                )
                if msg.type in ("error", "warning")
                else None,
            )

            def on_request_failed(request):
                failure = request.failure
                if isinstance(failure, str):
                    err = failure
                elif failure is not None:
                    err = getattr(failure, "error_text", str(failure))
                else:
                    err = "unknown"
                failed_requests.append({"url": request.url, "error": err})

            page.on("requestfailed", on_request_failed)

            print(f"Testing {page_url} -> {local_url}")
            try:
                page.goto(local_url, wait_until="networkidle", timeout=120000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                page_errors.append(f"Navigation failed: {exc}")

            # Filter out acceptable external link attempts (smugmug etc.)
            external_noise = (
                "smugmug.com",
                "squarespace.com",
                "google-analytics",
                "googletagmanager",
                "yui: NOT loaded",
                "audiocontext was not allowed",
                "encrypted media access has been blocked",
                "permissions policy violation",
                "p.typekit.net",
                "requeststorageaccess",
                "form context script not found",
            )
            filtered_console = [
                c
                for c in console_issues
                if not any(n in c["text"].lower() for n in external_noise)
            ]
            filtered_failed = [
                r
                for r in failed_requests
                if r["url"].startswith("http://localhost:8765/")
            ]

            filtered_page_errors = [
                e
                for e in page_errors
                if not any(n in e.lower() for n in external_noise)
            ]

            report["pages"][page_url] = {
                "local": local_url,
                "page_errors": filtered_page_errors,
                "console": filtered_console,
                "failed_requests": filtered_failed,
            }
            report["summary"]["errors"] += len(filtered_page_errors) + len(
                [c for c in filtered_console if c["type"] == "error"]
            )
            report["summary"]["warnings"] += len(
                [c for c in filtered_console if c["type"] == "warning"]
            )
            report["summary"]["failed_requests"] += len(filtered_failed)
            page.close()

        browser.close()

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {REPORT_PATH}")

    if report["summary"]["errors"] or report["summary"]["failed_requests"]:
        print("\nIssues found:")
        for url, data in report["pages"].items():
            if data["page_errors"] or data["console"] or data["failed_requests"]:
                print(f"\n{url}")
                for e in data["page_errors"]:
                    print(f"  PAGE ERROR: {e}")
                for c in data["console"]:
                    print(f"  CONSOLE {c['type'].upper()}: {c['text']}")
                for r in data["failed_requests"]:
                    print(f"  FAILED: {r['url']} ({r['error']})")
        return 1
    print("\nAll pages passed with no local console errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
