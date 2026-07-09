# philiphaebler.com Static Archive

Offline static mirror of [philiphaebler.com](https://www.philiphaebler.com/) exported from Squarespace.

## Quick start

```bash
# Build the archive (requires network access to the live site)
./scripts/run.sh

# Or manually:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python scripts/archive.py
.venv/bin/python -m http.server 8765 --directory site
```

Open http://localhost:8765/

## Deploy

Upload the entire `site/` directory to your web server document root.

### GitHub Pages

This repo publishes from the `docs/` folder to
https://whitnelson.github.io/philiphaebler/

After re-archiving, sync the build output into `docs/`, then run:

```bash
python3 scripts/patch_github_pages.py
```

That adds `.nojekyll`, rewrites asset paths to neutral names (so ad blockers
do not match `squarespace.com` in URLs), uses absolute `/philiphaebler/...`
paths, and injects a runtime rewriter for any remaining CDN references.

Apache users: `.htaccess` is included for directory index routing.

Nginx example:

```nginx
location / {
  try_files $uri $uri/ $uri/index.html =404;
}
```

## What is included

- All public pages (home, galleries, travel sections, blog, about, contact)
- Squarespace CSS, JavaScript, and template assets
- Adobe Typekit fonts (stored under `/indexaf/` and `/p.typekit.net/`)
- All gallery images from the sitemap and runtime discovery
- Offline patches for Squarespace analytics/API calls that would fail without a backend

## What does not work offline

- Contact form submission (requires Squarespace backend)
- External links (SmugMug, etc.) still point to live external sites by design

## Project layout

| Path | Purpose |
|------|---------|
| `site/` | Static website ready to deploy |
| `scripts/archive.py` | Crawler, downloader, and URL rewriter |
| `scripts/test_site.py` | Playwright console-error tests |
| `scripts/run.sh` | Full archive + test pipeline |
| `manifest.json` | URL mapping manifest from last archive run |
| `test-report.json` | Last automated test results |

## Re-archiving

If the live site changes, re-run `./scripts/run.sh`. The script replaces `site/` on each run.
# philiphaebler

