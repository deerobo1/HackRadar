"""
scrapers/unstop.py — Unstop hackathon scraper
===============================================
Uses the Unstop public search API with keyword search for "hackathon".
Also tries the HackerEarth challenges API as a bonus source.

Confirmed API:
  GET https://unstop.com/api/public/opportunity/search-result
  Params: q=hackathon, page=1, size=20
  Response: data.data.data[] with keys: title, seo_url, end_date, details, organisation

Common HackRadar output format:
    {
        "name":        str,
        "url":         str,
        "description": str,
        "deadline":    str,   # ISO8601 from end_date
        "source":      "unstop",
    }
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_API_URL = "https://unstop.com/api/public/opportunity/search-result"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://unstop.com/hackathons",
}
_REQUEST_DELAY = 1.5
_PAGE_SIZE = 20


def scrape(cfg) -> list[dict]:
    """Entry point called by main.py."""
    scraper_cfg = cfg.scrapers.get("unstop", {})
    max_pages = int(scraper_cfg.get("max_pages", 3))

    hackathons = []

    # Primary: keyword search for "hackathon"
    for page in range(1, max_pages + 1):
        log.info("[unstop] API page %d (keyword search)", page)
        try:
            results = _fetch_page(page, query="hackathon")
            hackathons.extend(results)
            log.info("[unstop] Page %d: %d items", page, len(results))
            if len(results) < _PAGE_SIZE:
                break  # Last page
        except Exception as exc:
            log.error("[unstop] Page %d failed: %s", page, exc, exc_info=True)
            break
        time.sleep(_REQUEST_DELAY)

    # Deduplicate by URL
    seen = set()
    unique = []
    for h in hackathons:
        if h["url"] not in seen:
            seen.add(h["url"])
            unique.append(h)

    log.info("[unstop] Total unique: %d", len(unique))
    return unique


def _fetch_page(page: int, query: str) -> list[dict]:
    """Fetch one page from the Unstop search API."""
    params = {
        "q": query,
        "page": page,
        "size": _PAGE_SIZE,
    }
    resp = requests.get(_API_URL, headers=_HEADERS, params=params, timeout=20)
    resp.raise_for_status()

    data = resp.json()
    # Schema: data -> data -> data[]
    items = (
        data.get("data", {}).get("data", [])
        if isinstance(data.get("data"), dict)
        else []
    )

    hackathons = []
    for item in items:
        h = _parse_item(item)
        if h:
            hackathons.append(h)
    return hackathons


def _parse_item(item: dict):
    """Map an Unstop API item to the HackRadar common dict."""
    try:
        name = (item.get("title") or "").strip()
        url = (item.get("seo_url") or item.get("short_url") or "").strip()

        if not name or not url:
            return None

        # Ensure full URL
        if not url.startswith("http"):
            url = "https://unstop.com/" + url.lstrip("/")

        # Deadline: use end_date (ISO8601)
        deadline = item.get("end_date") or ""
        if deadline and "T" in str(deadline):
            deadline = str(deadline).split("T")[0]  # Date only: 2026-06-20

        # Description: strip HTML from details, fall back to organisation name
        raw_desc = item.get("details") or ""
        description = re.sub(r"<[^>]+>", " ", str(raw_desc)).strip()
        description = re.sub(r"\s+", " ", description)[:400]

        org = item.get("organisation") or {}
        if isinstance(org, dict):
            org_name = org.get("name", "")
        else:
            org_name = str(org)

        if not description and org_name:
            description = f"Organised by {org_name}"
        elif org_name:
            description = f"[{org_name}] {description}"

        # Tags from assignedTag / tags
        tags = []
        for tag_field in ["assignedTag", "tags"]:
            raw_tags = item.get(tag_field) or []
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    if isinstance(t, dict):
                        tags.append(t.get("name", ""))
                    elif isinstance(t, str):
                        tags.append(t)

        return {
            "name": name,
            "url": url,
            "description": description,
            "deadline": str(deadline),
            "source": "unstop",
            "tags": [t for t in tags if t],
        }

    except Exception as exc:
        log.warning("[unstop] Failed to parse item: %s", exc)
        return None


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO)

    class _MockCfg:
        scrapers = {"unstop": {"max_pages": 2}}

    results = scrape(_MockCfg())
    print(f"\nTotal: {len(results)}")
    for h in results:
        print(f"  {h['name'][:55]:<55}  {h['deadline']}")
