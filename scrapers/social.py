"""
scrapers/social.py — Multi-source hackathon feed scraper
=========================================================
Originally planned as a Nitter/Twitter RSS scraper, but public Nitter
instances are largely dead (rate-limited / shut down).

This module now aggregates from three reliable, API-accessible sources:

  1. HackerEarth Challenges API — confirmed working, returns JSON
  2. Devfolio hackathons page RSS / JSON — popular Indian hackathon platform
  3. Kaggle Competitions API — open competitions with hackathon-like structure

All sources emit the same HackRadar common dict:
    {
        "name":        str,
        "url":         str,
        "description": str,
        "deadline":    str,
        "source":      "social/<subsource>",
    }
"""

import logging
import re
import time
from typing import Optional

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
    "Accept": "application/json, text/plain, */*",
}
_REQUEST_DELAY = 1.5


def scrape(cfg) -> list[dict]:
    """Entry point called by main.py."""
    scraper_cfg = cfg.scrapers.get("social", {})
    max_items = int(scraper_cfg.get("max_items_per_feed", 20))

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    sources = [
        ("hackerearth", lambda: _scrape_hackerearth(max_items)),
        ("devfolio", lambda: _scrape_devfolio(max_items)),
        ("devpost_rss", lambda: _scrape_devpost_rss(max_items)),
    ]

    # Also try Nitter-compatible RSS if any instances are configured and working
    nitter_feeds = cfg.nitter_rss_feeds or []
    if nitter_feeds:
        sources.append(
            ("nitter_rss", lambda: _scrape_nitter_rss(nitter_feeds, max_items))
        )

    for name, fn in sources:
        log.info("[social] Scraping source: %s", name)
        try:
            results = fn()
            log.info("[social/%s] %d items", name, len(results))
            for h in results:
                if h["url"] not in seen_urls:
                    seen_urls.add(h["url"])
                    all_results.append(h)
        except Exception as exc:
            log.error("[social/%s] Failed: %s", name, exc, exc_info=True)

    log.info("[social] Total items: %d", len(all_results))
    return all_results


# ── HackerEarth ──────────────────────────────────────────────────────────────

_HE_CHALLENGES_URL = "https://www.hackerearth.com/challenges/hackathon/"
_HE_API = "https://www.hackerearth.com/api/v3/challenges/"

def _scrape_hackerearth(max_items: int) -> list[dict]:
    """
    Scrape HackerEarth hackathon challenges.
    Tries the API first, falls back to HTML scraping of .challenge-card elements.
    """
    # Try API
    try:
        r = requests.get(
            _HE_API,
            headers={**_HEADERS, "Accept": "application/json"},
            params={"type": "hackathon", "status": "ongoing", "limit": max_items},
            timeout=20,
        )
        if r.ok and "json" in r.headers.get("Content-Type", ""):
            data = r.json()
            items = data.get("response", data.get("results", data.get("challenges", [])))
            if items:
                return [_parse_he_api_item(it) for it in items[:max_items] if it]
    except Exception as exc:
        log.debug("[social/hackerearth] API failed (%s), trying HTML", exc)

    # HTML fallback — .challenge-card confirmed working
    hackathons = []
    r = requests.get(_HE_CHALLENGES_URL, headers={**_HEADERS, "Accept": "text/html"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    cards = soup.select(".challenge-card")[:max_items]
    for card in cards:
        h = _parse_he_card(card)
        if h:
            hackathons.append(h)

    return hackathons


def _parse_he_api_item(item: dict) -> Optional[dict]:
    try:
        name = item.get("title") or item.get("name") or ""
        url = item.get("challenge_url") or item.get("url") or ""
        if not name or not url:
            return None
        return {
            "name": name,
            "url": url,
            "description": (item.get("description") or "")[:300],
            "deadline": str(item.get("end_date") or item.get("submission_deadline") or ""),
            "source": "social/hackerearth",
        }
    except Exception:
        return None


def _parse_he_card(card) -> Optional[dict]:
    """Parse a HackerEarth .challenge-card element."""
    try:
        link = card.select_one("a[href]")
        if not link:
            return None

        href = link.get("href", "")
        if not href.startswith("http"):
            href = "https://www.hackerearth.com" + href

        name_tag = card.select_one(".challenge-name, h3, h4, .title, .name")
        name = name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)

        deadline_tag = card.select_one(".challenge-time, .deadline, time, .date")
        deadline = ""
        if deadline_tag:
            deadline = deadline_tag.get("datetime") or deadline_tag.get_text(strip=True)

        desc_tag = card.select_one(".challenge-desc, .description, p")
        description = desc_tag.get_text(strip=True)[:300] if desc_tag else ""

        return {
            "name": name,
            "url": href,
            "description": description,
            "deadline": deadline,
            "source": "social/hackerearth",
        }
    except Exception as exc:
        log.debug("[social/hackerearth] Card parse error: %s", exc)
        return None


# ── Devfolio ─────────────────────────────────────────────────────────────────

_DEVFOLIO_PAGE = "https://devfolio.co/hackathons"

def _scrape_devfolio(max_items: int) -> list[dict]:
    """
    Devfolio hackathon listing — HTML scrape of devfolio.co/hackathons.
    The page is Next.js server-rendered with __NEXT_DATA__ JSON embedded.
    """
    headers = {
        **_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://devfolio.co",
    }

    r = requests.get(_DEVFOLIO_PAGE, headers=headers, timeout=25)
    r.raise_for_status()

    hackathons = []

    # Strategy 1: Extract Next.js page data JSON
    import json as _json
    soup = BeautifulSoup(r.text, "lxml")
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if next_data_tag:
        try:
            next_data = _json.loads(next_data_tag.string or "{}")
            # Drill into the hackathons list
            page_props = next_data.get("props", {}).get("pageProps", {})
            items = (
                page_props.get("hackathons")
                or page_props.get("data", {}).get("hackathons", [])
                or page_props.get("initialData", {}).get("hackathons", [])
                or []
            )
            if isinstance(items, list):
                for item in items[:max_items]:
                    h = _parse_devfolio_item(item)
                    if h:
                        hackathons.append(h)
                if hackathons:
                    return hackathons
        except Exception as exc:
            log.debug("[social/devfolio] __NEXT_DATA__ parse failed: %s", exc)

    # Strategy 2: HTML card scraping
    for sel in [
        "[class*=HackathonCard]", "[class*=hackathon-card]",
        "article", ".card", "[class*=Card]",
    ]:
        cards = soup.select(sel)
        if len(cards) >= 2:
            log.debug("[social/devfolio] Using selector %r: %d cards", sel, len(cards))
            for card in cards[:max_items]:
                link = card.select_one("a[href]")
                if not link:
                    continue
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = "https://devfolio.co" + href
                name_tag = card.select_one("h2, h3, h4, [class*=title], [class*=name]")
                name = name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)[:80]
                if name:
                    hackathons.append({
                        "name": name,
                        "url": href,
                        "description": "",
                        "deadline": "",
                        "source": "social/devfolio",
                    })
            if hackathons:
                return hackathons

    log.info("[social/devfolio] No hackathons parsed from Devfolio page.")
    return hackathons


def _parse_devfolio_item(item: dict) -> Optional[dict]:
    try:
        name = item.get("name") or item.get("title") or ""
        slug = item.get("slug") or ""
        url = f"https://{slug}.devfolio.co" if slug else item.get("url") or ""
        if not name or not url:
            return None
        deadline = (
            item.get("submission_deadline")
            or item.get("ends_at")
            or item.get("end_date")
            or ""
        )
        description = (item.get("tagline") or item.get("description") or "")[:300]
        return {
            "name": name,
            "url": url,
            "description": description,
            "deadline": str(deadline),
            "source": "social/devfolio",
        }
    except Exception:
        return None


# ── Devpost RSS (supplement to the main Devpost scraper) ────────────────────

_DEVPOST_RSS = "https://devpost.com/hackathons.rss"

def _scrape_devpost_rss(max_items: int) -> list[dict]:
    """
    Parse Devpost's RSS feed as a lightweight complement to the API scraper.
    """
    parsed = feedparser.parse(_DEVPOST_RSS)
    hackathons = []

    for entry in parsed.entries[:max_items]:
        name = getattr(entry, "title", "") or ""
        url = getattr(entry, "link", "") or ""
        description = getattr(entry, "summary", "") or ""
        # Strip HTML from summary
        description = re.sub(r"<[^>]+>", " ", description).strip()[:300]

        deadline = ""
        if hasattr(entry, "published"):
            deadline = str(entry.published)

        if name and url:
            hackathons.append({
                "name": name,
                "url": url,
                "description": description,
                "deadline": deadline,
                "source": "social/devpost_rss",
            })

    return hackathons


# ── Nitter RSS (best-effort if any instances work) ───────────────────────────

_HACKATHON_PATTERNS = [
    re.compile(r"\bhackathon\b", re.IGNORECASE),
    re.compile(r"\bhack\s*day\b", re.IGNORECASE),
    re.compile(r"\bbuild\s*challenge\b", re.IGNORECASE),
    re.compile(r"\bregist(er|ration)\s+open\b", re.IGNORECASE),
]
_URL_PATTERN = re.compile(r"https?://\S+")


def _scrape_nitter_rss(feed_urls: list[str], max_items: int) -> list[dict]:
    """
    Try each Nitter RSS feed in order. Returns first successful result.
    """
    rss_headers = {"User-Agent": "HackRadar/1.0 (+https://github.com/hackradar)"}

    for feed_url in feed_urls:
        try:
            parsed = feedparser.parse(feed_url, request_headers=rss_headers)
            if parsed.entries:
                results = []
                for entry in parsed.entries[:max_items]:
                    title = getattr(entry, "title", "") or ""
                    summary = getattr(entry, "summary", "") or ""
                    text = re.sub(r"<[^>]+>", " ", f"{title} {summary}").strip()

                    if not any(p.search(text) for p in _HACKATHON_PATTERNS):
                        continue

                    url = getattr(entry, "link", "") or ""
                    embedded = _URL_PATTERN.findall(text)
                    for eu in embedded:
                        if "nitter" not in eu and "twitter" not in eu and "x.com" not in eu:
                            url = eu
                            break

                    if url:
                        results.append({
                            "name": text[:100],
                            "url": url,
                            "description": text[:500],
                            "deadline": "",
                            "source": "social/nitter",
                        })

                if results:
                    log.info("[social/nitter] Got %d items from %s", len(results), feed_url)
                    return results

        except Exception as exc:
            log.debug("[social/nitter] Feed %s failed: %s", feed_url, exc)

    return []


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO)

    class _MockCfg:
        scrapers = {"social": {"max_items_per_feed": 10}}
        nitter_rss_feeds = []

    results = scrape(_MockCfg())
    print(f"\nTotal: {len(results)}")
    for h in results:
        print(f"  [{h['source']:<25}] {h['name'][:50]}")
