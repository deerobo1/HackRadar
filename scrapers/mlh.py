"""
scrapers/mlh.py — MLH season events scraper
=============================================
MLH renders its events page with Tailwind CSS (no semantic class names)
and hydrates content server-side. Event links are embedded directly in
<a href="https://events.mlh.io/events/..."> anchor tags with name + date
packed into the anchor text.

Strategy:
  1. Fetch https://mlh.io/seasons/2026/events (current year, auto-incremented)
  2. Extract all anchors pointing to events.mlh.io
  3. Parse name + date from the anchor text
  4. Fallback to the MLH Fellowship RSS feed if no events found

Common HackRadar output format:
    {
        "name":        str,
        "url":         str,
        "description": str,
        "deadline":    str,   # date range from anchor text
        "source":      "mlh",
    }
"""

import logging
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
import feedparser
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}
_EVENTS_BASE = "https://events.mlh.io/events"
_SEASON_URLS = [
    f"https://mlh.io/seasons/{datetime.now().year}/events",
    f"https://mlh.io/seasons/{datetime.now().year + 1}/events",
]
# Fallback RSS from MLH's event calendar (ICS/RSS if available)
_MLH_RSS_FEEDS = [
    "https://mlh.io/events/rss",
    "https://mlh.io/seasons/rss",
]


def scrape(cfg) -> list[dict]:
    """Entry point called by main.py."""
    log.info("[mlh] Starting MLH scrape")

    # Try all season URLs
    for url in _SEASON_URLS:
        try:
            hackathons = _scrape_season_page(url)
            if hackathons:
                log.info("[mlh] Got %d events from %s", len(hackathons), url)
                return hackathons
        except Exception as exc:
            log.warning("[mlh] Season page %s failed: %s", url, exc)

    # Fallback: RSS feeds
    for feed_url in _MLH_RSS_FEEDS:
        try:
            hackathons = _scrape_rss(feed_url)
            if hackathons:
                log.info("[mlh] Got %d events from RSS: %s", len(hackathons), feed_url)
                return hackathons
        except Exception as exc:
            log.warning("[mlh] RSS %s failed: %s", feed_url, exc)

    log.warning("[mlh] No events found from any source.")
    return []


def _scrape_season_page(page_url: str) -> list[dict]:
    """
    Scrape a MLH season events page.
    Extracts anchor tags pointing to events.mlh.io and parses their text.
    """
    resp = requests.get(page_url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    hackathons = []
    seen_urls: set = set()

    # All anchors pointing to MLH events
    for a_tag in soup.find_all("a", href=re.compile(r"events\.mlh\.io/events/")):
        href = a_tag.get("href", "").strip()
        # Strip UTM params to get clean URL
        clean_url = _clean_url(href)

        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        # The anchor text contains "Event Name\nJAN 01 - 05\nCity, Country" etc.
        full_text = a_tag.get_text(separator="\n", strip=True)
        name, deadline, location = _parse_mlh_anchor_text(full_text)

        if not name:
            continue

        description = ""
        if location:
            description = f"Location: {location}"

        hackathons.append({
            "name": name,
            "url": clean_url,
            "description": description,
            "deadline": deadline,
            "source": "mlh",
        })

    return hackathons


def _parse_mlh_anchor_text(text: str) -> tuple[str, str, str]:
    """
    Parse MLH anchor text like:
      "Global Hack Week: Hacking for Good\nJUN 12 - 18\nEverywhere, Worldwide"

    Returns (name, deadline, location).
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return "", "", ""

    name = lines[0]

    # Find date pattern: "JUN 12 - 18" or "JUN 12 - JUL 2"
    date_pattern = re.compile(
        r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d{1,2}"
        r"(?:\s*[-–]\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)?\s*\d{1,2})?",
        re.IGNORECASE,
    )
    deadline = ""
    location = ""

    for line in lines[1:]:
        if date_pattern.search(line):
            deadline = line.strip()
        elif line and not deadline and any(c.isdigit() for c in line):
            # Could be a date we haven't matched yet
            pass
        elif line and deadline:
            # Anything after the date is the location
            location = line
            break
        elif line and not any(c.isdigit() for c in line):
            # Non-date text before the date — might be subtitle
            pass

    return name, deadline, location


def _clean_url(url: str) -> str:
    """Strip UTM parameters from a URL."""
    parsed = urlparse(url)
    # Rebuild without query string
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _scrape_rss(feed_url: str) -> list[dict]:
    """Fallback RSS scraper for MLH feeds."""
    parsed = feedparser.parse(feed_url)
    hackathons = []

    for entry in parsed.entries:
        name = getattr(entry, "title", "") or ""
        url = getattr(entry, "link", "") or ""
        description = getattr(entry, "summary", "") or ""
        deadline = ""

        # Try to extract date from published
        if hasattr(entry, "published"):
            deadline = str(entry.published)

        if name and url:
            hackathons.append({
                "name": name,
                "url": url,
                "description": description[:300],
                "deadline": deadline,
                "source": "mlh",
            })

    return hackathons


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO)

    class _MockCfg:
        scrapers = {"mlh": {}}

    results = scrape(_MockCfg())
    print(f"\nTotal: {len(results)}")
    for h in results:
        print(f"  {h['name'][:55]:<55}  {h['deadline']}")
