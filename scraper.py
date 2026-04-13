#!/usr/bin/env python3
"""
etender.gov.az scraper — uses the real JSON API discovered via DevTools:
  GET /events?EventType=0|2&PageSize=15&PageNumber=N&EventStatus=1
              &Keyword=...&publishDateFrom=...&publishDateTo=...
"""
import json, os, sys, time, urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

# ─── Load config ─────────────────────────────────────────────────────────────
with open("config.json") as f:
    cfg = json.load(f)

BASE_URL   = cfg["base_url"]
SEARCH_URL = cfg["search_url"]
COUNT_URL  = cfg["count_url"]
PAGE_SIZE  = int(cfg.get("page_size", 15))
MAX_PAGES  = int(os.environ.get("MAX_PAGES", cfg.get("max_pages", 20)))

# Dates default to last 30 days if not provided
today     = date.today()
ago30     = today - timedelta(days=30)
KEYWORD   = os.environ.get("KEYWORD",   cfg.get("default_keyword",   ""))
# Dates must be in ISO 8601 format: 2026-04-01T00:00:00.000Z
def to_iso(d):
    """Accept YYYY-MM-DD or already-formatted ISO string, return full ISO with time."""
    if not d:
        return ""
    if "T" in str(d):
        return str(d)
    return str(d) + "T00:00:00.000Z"

DATE_FROM = to_iso(os.environ.get("DATE_FROM", cfg.get("default_date_from", ago30.isoformat())))
DATE_TO   = to_iso(os.environ.get("DATE_TO",   cfg.get("default_date_to",   today.isoformat())))
EVENT_TYPES = cfg.get("event_types", [0, 2])

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "az,en;q=0.9",
    "Referer":         BASE_URL + "/main/",
}

session = requests.Session()
session.headers.update(HEADERS)

def warm_up():
    try:
        r = session.get(BASE_URL + "/main/", timeout=20)
        print(f"  Warm-up: {r.status_code}")
    except Exception as e:
        print(f"  Warm-up failed (continuing): {e}")

def build_url(template, event_type, page=1):
    return (template
        .replace("{EVENT_TYPE}", str(event_type))
        .replace("{PAGE}",       str(page))
        .replace("{KEYWORD}",    urllib.parse.quote(KEYWORD))
        .replace("{DATE_FROM}",  DATE_FROM)
        .replace("{DATE_TO}",    DATE_TO))

def get_count(event_type):
    url = build_url(COUNT_URL, event_type)
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, int):
            return data
        if isinstance(data, dict):
            return data.get("count") or data.get("total") or data.get("totalCount") or 0
    except Exception as e:
        print(f"  Count error (EventType={event_type}): {e}")
    return None

def normalise(item, event_type):
    def pick(*keys):
        for k in keys:
            v = item.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    tender_id  = pick("id", "tenderId", "eventId", "Id", "number")
    title      = pick("name", "title", "subject", "eventName", "Name", "Title")
    org        = pick("buyerOrganizationName", "organization", "buyer", "customerName", "BuyerOrganizationName")
    date_pub   = pick("publishDate", "datePublished", "startDate", "PublishDate", "createdDate")
    deadline   = pick("deadline", "endDate", "submissionDeadline", "Deadline", "EndDate")
    status_raw = pick("status", "eventStatus", "Status", "state")
    doc_number = pick("documentNumber", "docNumber", "lotNumber")

    detail_url = ""
    url_slug   = pick("url", "link", "detailUrl")
    if url_slug:
        detail_url = url_slug if url_slug.startswith("http") else BASE_URL.rstrip("/") + "/" + url_slug.lstrip("/")
    elif tender_id:
        detail_url = f"{BASE_URL}/tender/detail/{tender_id}"

    s = status_raw.lower()
    if any(x in s for x in ["active", "open", "açıq", "aktiv", "1"]):
        status = "open"
    elif any(x in s for x in ["closed", "bağlı", "0", "finish"]):
        status = "closed"
    else:
        status = "open" if status_raw == "" else status_raw

    type_label = {0: "Open Tender", 2: "e-Tender"}.get(event_type, f"Type {event_type}")

    return {
        "id":              tender_id,
        "document_number": doc_number,
        "title":           title,
        "organization":    org,
        "type":            type_label,
        "date_published":  date_pub,
        "deadline":        deadline,
        "status":          status,
        "url":             detail_url,
        "_raw":            item,
    }

def scrape_event_type(event_type):
    results = []
    print(f"\n── EventType={event_type} ──────────────────────────────")

    total = get_count(event_type)
    if total is not None:
        pages_needed = min(MAX_PAGES, -(-int(total) // PAGE_SIZE))
        print(f"  Count: {total} → {pages_needed} page(s)")
    else:
        pages_needed = MAX_PAGES
        print(f"  Count unknown → will try up to {MAX_PAGES} page(s)")

    for page in range(1, pages_needed + 1):
        url = build_url(SEARCH_URL, event_type, page)
        print(f"  Page {page}/{pages_needed}: {url[:100]}…")
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  ERROR: {e}")
            break

        try:
            data = r.json()
        except ValueError:
            print(f"  Not JSON. First 300 chars: {r.text[:300]}")
            break

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "data", "results", "events", "tenders", "list"):
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if not items:
                for v in data.values():
                    if isinstance(v, list) and len(v) > 0:
                        items = v
                        break

        print(f"  → {len(items)} items")
        if not items:
            break

        for item in items:
            results.append(normalise(item, event_type))

        time.sleep(0.8)

    return results

def main():
    print(f"etender.gov.az scraper")
    print(f"  keyword={KEYWORD!r}  from={DATE_FROM}  to={DATE_TO}  types={EVENT_TYPES}")

    warm_up()

    all_results = []
    for et in EVENT_TYPES:
        all_results.extend(scrape_event_type(et))

    seen, deduped = set(), []
    for r in all_results:
        key = r["id"] or r["title"]
        if key and key not in seen:
            seen.add(key)
            r.pop("_raw", None)
            deduped.append(r)

    print(f"\nTotal: {len(all_results)} scraped, {len(deduped)} after dedup")

    Path("data").mkdir(exist_ok=True)
    output = {
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "keyword":    KEYWORD,
        "date_from":  DATE_FROM,
        "date_to":    DATE_TO,
        "count":      len(deduped),
        "results":    deduped,
    }
    with open("data/tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("Wrote data/tenders.json ✓")

    if not deduped:
        print("\nWARNING: 0 results. Add  print(r.json())  in scrape_event_type() to debug raw response.")
        sys.exit(1)

if __name__ == "__main__":
    main()
