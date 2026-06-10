"""
scrapers/devpost.py — Devpost hackathon scraper
================================================
Uses the Devpost JSON API (/api/hackathons) rather than HTML scraping.
The API returns clean structured data: title, url, dates, prizes, themes.

Common HackRadar output format:
    {
        "name":        str,
        "url":         str,
        "description": str,   # themes + organisation + location
        "deadline":    str,   # e.g. "May 05 - Jun 11, 2026"
        "source":      "devpost",
        "prize":       str,   # optional enrichment
        "tags":        list,  # theme names
    }
"""

import logging
import time
from typing import Optional
import re

import requests

log = logging.getLogger(__name__)

_API_URL = "https://devpost.com/api/hackathons"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://devpost.com/hackathons",
}
_REQUEST_DELAY = 1.5  # seconds between page requests


def scrape(cfg) -> list[dict]:
    """
    Entry point called by main.py.
    Reads max_pages from cfg.scrapers["devpost"].
    """
    scraper_cfg = cfg.scrapers.get("devpost", {})
    max_pages = int(scraper_cfg.get("max_pages", 3))

    hackathons: list[dict] = []
    page = 1

    while page <= max_pages:
        params = {
            "status": "open",
            "order_by": "recently-added",
            "page": page,
        }
        log.info("[devpost] Fetching API page %d", page)

        try:
            resp = requests.get(_API_URL, headers=_HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("hackathons", [])
            if not items:
                log.info("[devpost] No hackathons on page %d — stopping.", page)
                break

            for item in items:
                h = _parse_item(item)
                if h:
                    hackathons.append(h)

            # Check if there are more pages
            meta = data.get("meta", {})
            total = int(meta.get("total_count", 0))
            per_page = int(meta.get("per_page", 9))
            fetched_so_far = page * per_page
            log.info(
                "[devpost] Page %d: %d items (total=%d, fetched≈%d)",
                page, len(items), total, fetched_so_far,
            )

            if fetched_so_far >= total:
                log.info("[devpost] All hackathons fetched.")
                break

            page += 1
            time.sleep(_REQUEST_DELAY)

        except Exception as exc:
            log.error("[devpost] API error on page %d: %s", page, exc, exc_info=True)
            break

    log.info("[devpost] Total scraped: %d", len(hackathons))
    return hackathons


def _parse_item(item: dict) -> Optional[dict]:
    """Map a Devpost API item to the HackRadar common dict."""
    try:
        name = item.get("title", "").strip()
        url = item.get("url", "").strip()

        if not name or not url:
            return None

        # Deadline / dates
        deadline = item.get("submission_period_dates", "") or item.get("time_left_to_submission", "")

        # Description: combine themes, org, and location
        themes = [t.get("name", "") for t in item.get("themes", []) if t.get("name")]
        org = item.get("organization_name", "")
        location = item.get("displayed_location", {}).get("location", "")

        desc_parts = []
        if org:
            desc_parts.append(f"Organiser: {org}")
        if location:
            desc_parts.append(f"Location: {location}")
        if themes:
            desc_parts.append(f"Themes: {', '.join(themes)}")
        description = " | ".join(desc_parts)

        # Prize amount — strip HTML tags
        prize_raw = item.get("prize_amount", "") or ""
        prize = re.sub(r"<[^>]+>", "", prize_raw).strip()

        return {
            "name": name,
            "url": url,
            "description": description,
            "deadline": deadline,
            "source": "devpost",
            "prize": prize,
            "tags": themes,
        }

    except Exception as exc:
        log.warning("[devpost] Failed to parse item: %s", exc)
        return None


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO)

    class _MockCfg:
        scrapers = {"devpost": {"max_pages": 2}}

    results = scrape(_MockCfg())
    print(f"\nTotal: {len(results)}")
    for h in results:
        print(f"  {h['name'][:55]:<55}  {h['deadline']}")
