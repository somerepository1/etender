#!/usr/bin/env python3
"""
etender.gov.az scraper
- Loops through all keywords defined in config.json
- Date range defaults to last 7 days (overridable via env vars)
- Merges and deduplicates all results into data/tenders.json
"""
import json, os, sys, time, urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

# ─── Config ───────────────────────────────────────────────────────────────────
with open("config.json") as f:
    cfg = json.load(f)

BASE_URL    = cfg["base_url"]
SEARCH_URL  = cfg["search_url"]
COUNT_URL   = cfg["count_url"]
PAGE_SIZE   = int(cfg.get("page_size", 15))
MAX_PAGES   = int(os.environ.get("MAX_PAGES", cfg.get("max_pages", 20)))
EVENT_TYPES = cfg.get("event_types", [0, 2])

# ─── Keywords ─────────────────────────────────────────────────────────────────
# Priority: env var KEYWORDS (comma-separated) → config.json "keywords" list
_kw_env = os.environ.get("KEYWORDS", "").strip()
if _kw_env:
    KEYWORDS = [k.strip() for k in _kw_env.split(",") if k.strip()]
else:
    KEYWORDS = cfg.get("keywords", [])

if not KEYWORDS:
    print("ERROR: No keywords defined. Add them to config.json → 'keywords' list.")
    sys.exit(1)

# ─── Date range ───────────────────────────────────────────────────────────────
today = date.today()
week_ago = today - timedelta(days=7)

def to_iso(d):
    if not d:
        return ""
    s = str(d)
    return s if "T" in s else s + "T00:00:00.000Z"

DATE_FROM = to_iso(os.environ.get("DATE_FROM", "") or week_ago.isoformat())
DATE_TO   = to_iso(os.environ.get("DATE_TO",   "") or today.isoformat())

# ─── HTTP session ─────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "az,en;q=0.9",
    "Referer":         BASE_URL + "/main/",
})

def warm_up():
    try:
        r = session.get(BASE_URL + "/main/", timeout=20)
        print(f"Session warm-up: HTTP {r.status_code}")
    except Exception as e:
        print(f"Warm-up failed (continuing): {e}")

# ─── URL builder ──────────────────────────────────────────────────────────────
def build_url(template, keyword, event_type, page=1):
    return (template
        .replace("{EVENT_TYPE}", str(event_type))
        .replace("{PAGE}",       str(page))
        .replace("{KEYWORD}",    urllib.parse.quote(keyword))
        .replace("{DATE_FROM}",  DATE_FROM)
        .replace("{DATE_TO}",    DATE_TO))

# ─── Count ────────────────────────────────────────────────────────────────────
def get_count(keyword, event_type):
    try:
        r = session.get(build_url(COUNT_URL, keyword, event_type), timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, (int, float)):
            return int(data)
        if isinstance(data, dict):
            for k in ("count", "total", "totalCount", "TotalCount"):
                if k in data:
                    return int(data[k])
    except Exception as e:
        print(f"    Count error: {e}")
    return None

# ─── Normalise ────────────────────────────────────────────────────────────────
def pick(item, *keys):
    for k in keys:
        v = item.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

def normalise(item, keyword, event_type):
    tender_id  = pick(item, "id", "tenderId", "eventId", "Id")
    title      = pick(item, "name", "title", "subject", "eventName", "Name")
    org        = pick(item, "buyerOrganizationName", "organization", "buyer", "BuyerOrganizationName")
    date_pub   = pick(item, "publishDate", "datePublished", "startDate", "PublishDate")
    deadline   = pick(item, "deadline", "endDate", "submissionDeadline", "Deadline")
    doc_number = pick(item, "documentNumber", "docNumber")
    status_raw = pick(item, "status", "eventStatus", "Status")

    url_slug   = pick(item, "url", "link", "detailUrl")
    detail_url = (url_slug if url_slug.startswith("http") else BASE_URL.rstrip("/") + "/" + url_slug.lstrip("/")) \
                 if url_slug else (f"{BASE_URL}/tender/detail/{tender_id}" if tender_id else "")

    s = status_raw.lower()
    status = "open" if any(x in s for x in ["active","open","açıq","aktiv","1"]) \
        else "closed" if any(x in s for x in ["closed","bağlı","0","finish"]) \
        else ("open" if not status_raw else status_raw)

    return {
        "id":              tender_id,
        "document_number": doc_number,
        "title":           title,
        "organization":    org,
        "type":            {0: "Open Tender", 2: "e-Tender"}.get(event_type, f"Type {event_type}"),
        "matched_keyword": keyword,
        "date_published":  date_pub,
        "deadline":        deadline,
        "status":          status,
        "url":             detail_url,
    }

# ─── Scrape one keyword + event type ─────────────────────────────────────────
def scrape_one(keyword, event_type):
    results = []
    total = get_count(keyword, event_type)
    pages = min(MAX_PAGES, -(-int(total) // PAGE_SIZE)) if total else MAX_PAGES
    label = f"  [{keyword}] EventType={event_type}"
    print(f"{label} → {total if total is not None else '?'} results, {pages} page(s)")

    for page in range(1, pages + 1):
        url = build_url(SEARCH_URL, keyword, event_type, page)
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    Page {page} error: {e}")
            break

        items = data if isinstance(data, list) else next(
            (v for k, v in data.items() if isinstance(v, list) and v),
            []
        ) if isinstance(data, dict) else []

        print(f"    Page {page}/{pages}: {len(items)} items")
        if not items:
            break

        for item in items:
            results.append(normalise(item, keyword, event_type))
        time.sleep(0.5)

    return results

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"etender.gov.az multi-keyword scraper")
    print(f"  Date range : {DATE_FROM}  →  {DATE_TO}")
    print(f"  Keywords   : {KEYWORDS}")
    print(f"  EventTypes : {EVENT_TYPES}")
    print("=" * 60)

    warm_up()

    all_results = []
    for keyword in KEYWORDS:
        print(f"\n▶ Keyword: '{keyword}'")
        for et in EVENT_TYPES:
            all_results.extend(scrape_one(keyword, et))
        time.sleep(1)

    # Deduplicate by tender id — keep first occurrence (preserves matched_keyword)
    seen, deduped = set(), []
    for r in all_results:
        key = r["id"] or (r["title"] + r["date_published"])
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)

    # Summary
    print(f"\n{'='*60}")
    print(f"Total scraped : {len(all_results)}")
    print(f"After dedup   : {len(deduped)}")
    kw_counts = {}
    for r in deduped:
        kw_counts[r["matched_keyword"]] = kw_counts.get(r["matched_keyword"], 0) + 1
    for kw, n in kw_counts.items():
        print(f"  {kw:<30} {n} results")
    print("=" * 60)

    Path("data").mkdir(exist_ok=True)
    output = {
        "scraped_at":  datetime.utcnow().isoformat() + "Z",
        "date_from":   DATE_FROM,
        "date_to":     DATE_TO,
        "keywords":    KEYWORDS,
        "count":       len(deduped),
        "results":     deduped,
    }
    with open("data/tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Wrote data/tenders.json ✓")

    if not deduped:
        print("WARNING: 0 results. Check field names with: print(r.json()) in scrape_one()")
        sys.exit(1)

if __name__ == "__main__":
    main()
