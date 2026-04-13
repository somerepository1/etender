#!/usr/bin/env python3
"""
etender.gov.az scraper
Runs in GitHub Actions — reads config.json, scrapes tenders, writes data/tenders.json
"""
import json, os, re, sys, time, urllib.parse
from datetime import datetime, date
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Installing dependencies...")
    os.system("pip install requests beautifulsoup4 lxml -q")
    import requests
    from bs4 import BeautifulSoup

# ─── Load config ─────────────────────────────────────────────────────────────
with open("config.json") as f:
    cfg = json.load(f)

KEYWORD   = os.environ.get("KEYWORD",   cfg.get("default_keyword", ""))
DATE_FROM = os.environ.get("DATE_FROM", cfg.get("default_date_from", ""))
DATE_TO   = os.environ.get("DATE_TO",   cfg.get("default_date_to",   ""))
MAX_PAGES = int(os.environ.get("MAX_PAGES", cfg.get("max_pages", 5)))

SEARCH_URL     = cfg["search_url"]      # URL pattern with {KEYWORD}, {DATE_FROM}, {DATE_TO}, {PAGE}
ROW_SELECTORS  = cfg["row_selectors"]   # list of CSS selectors to find tender rows
FIELD_MAP      = cfg["field_map"]       # maps field name → CSS selector within a row
BASE_URL       = cfg["base_url"]        # e.g. https://etender.gov.az

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "az,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

# ─── Build search URL ─────────────────────────────────────────────────────────
def build_url(page=1):
    url = SEARCH_URL
    url = url.replace("{KEYWORD}",   urllib.parse.quote(KEYWORD))
    url = url.replace("{DATE_FROM}", DATE_FROM)
    url = url.replace("{DATE_TO}",   DATE_TO)
    url = url.replace("{PAGE}",      str(page))
    return url

# ─── Extract a field from a row element ──────────────────────────────────────
def get_field(row, selector_or_idx):
    """selector_or_idx can be a CSS selector string or an integer (column index)"""
    if selector_or_idx is None:
        return ""
    if isinstance(selector_or_idx, int):
        cells = row.select("td, th")
        return cells[selector_or_idx].get_text(strip=True) if selector_or_idx < len(cells) else ""
    el = row.select_one(selector_or_idx)
    return el.get_text(strip=True) if el else ""

def get_link(row):
    a = row.select_one("a[href]")
    if not a:
        return ""
    href = a["href"]
    if href.startswith("http"):
        return href
    return BASE_URL.rstrip("/") + "/" + href.lstrip("/")

# ─── Parse one page ──────────────────────────────────────────────────────────
def parse_page(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []

    for sel in ROW_SELECTORS:
        found = soup.select(sel)
        if found:
            rows = found
            break

    # Fallback: any table rows with 3+ cells
    if not rows:
        rows = [tr for tr in soup.select("table tr")
                if len(tr.select("td")) >= 3]

    results = []
    for i, row in enumerate(rows):
        entry = {
            "id":             get_field(row, FIELD_MAP.get("id",   0)),
            "title":          get_field(row, FIELD_MAP.get("title", 1)),
            "organization":   get_field(row, FIELD_MAP.get("organization", 2)),
            "date_published": get_field(row, FIELD_MAP.get("date_published", 3)),
            "deadline":       get_field(row, FIELD_MAP.get("deadline", 4)),
            "type":           get_field(row, FIELD_MAP.get("type", "")),
            "status":         get_field(row, FIELD_MAP.get("status", "")),
            "url":            get_link(row),
        }
        # Skip empty/header rows
        if not entry["title"] or entry["title"].lower() in ("title", "subject", "başlıq", ""):
            continue
        results.append(entry)
    return results

# ─── Detect last page ─────────────────────────────────────────────────────────
def has_next_page(html, current_page):
    soup = BeautifulSoup(html, "lxml")
    # Common: pagination link with page+1
    next_links = soup.select(f'a[href*="page={current_page+1}"], .pagination .next:not(.disabled)')
    return len(next_links) > 0

# ─── Main scrape loop ─────────────────────────────────────────────────────────
def scrape():
    all_results = []
    print(f"Scraping: keyword={KEYWORD!r} from={DATE_FROM} to={DATE_TO} max_pages={MAX_PAGES}")

    for page in range(1, MAX_PAGES + 1):
        url = build_url(page)
        print(f"  Page {page}: {url}")
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  ERROR: {e}")
            break

        rows = parse_page(resp.text)
        print(f"  Found {len(rows)} rows")

        if not rows:
            print("  No rows — stopping.")
            break

        all_results.extend(rows)

        if not has_next_page(resp.text, page):
            print("  No next page — done.")
            break

        time.sleep(1.5)  # be polite

    return all_results

# ─── Write output ─────────────────────────────────────────────────────────────
def write_output(results):
    Path("data").mkdir(exist_ok=True)
    output = {
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "keyword":    KEYWORD,
        "date_from":  DATE_FROM,
        "date_to":    DATE_TO,
        "count":      len(results),
        "results":    results
    }
    with open("data/tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(results)} results → data/tenders.json")

if __name__ == "__main__":
    results = scrape()
    write_output(results)
    if not results:
        print("\nWARNING: 0 results scraped. Check config.json:")
        print("  1. Open etender.gov.az, search for something")
        print("  2. In Chrome DevTools → Network, find the search request URL")
        print("  3. Update 'search_url' in config.json with the real pattern")
        sys.exit(1)
