"""
scrapers/watchlist.py — Hash-diff page monitor
================================================
Monitors arbitrary URLs (company blogs, careers pages, etc.) for changes
that might indicate a new hackathon announcement. Uses MD5 hashing to
detect content changes without storing full page content.

How it works:
  1. Fetch the page
  2. Extract meaningful text (strip boilerplate nav/footer/ads)
  3. Compute MD5 of the text
  4. Compare against the stored hash in the local .watchlist_hashes file
  5. If changed AND "hackathon" keyword found → yield as a new item

Common HackRadar output format:
    {
        "name":        str,
        "url":         str,
        "description": str,
        "deadline":    "",   # not knowable from a generic page change
        "source":      "watchlist",
    }
"""

import hashlib
import json
import logging
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
_HASH_STORE = Path(".watchlist_hashes.json")
_REQUEST_DELAY = 2.0
_HACKATHON_KEYWORDS = {
    "hackathon", "hack", "challenge", "contest", "competition",
    "build", "sprint", "code jam", "codejam",
}


def _load_hashes() -> dict:
    """Load stored URL→hash mapping from disk."""
    if _HASH_STORE.exists():
        try:
            return json.loads(_HASH_STORE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_hashes(hashes: dict) -> None:
    """Persist URL→hash mapping to disk."""
    _HASH_STORE.write_text(
        json.dumps(hashes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _page_hash(text: str) -> str:
    """MD5 of normalised page text."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _extract_text(html: str) -> str:
    """
    Extract meaningful text from an HTML page.
    Strips nav, footer, script, style, and other boilerplate.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise elements
    for tag in soup.select(
        "nav, footer, header, aside, script, style, noscript, "
        ".cookie-banner, .ad, .advertisement, .sidebar"
    ):
        tag.decompose()

    return soup.get_text(separator=" ", strip=True).lower()


def _has_hackathon_keywords(text: str) -> bool:
    """Return True if the page text contains any hackathon-related keywords."""
    words = set(text.split())
    return bool(_HACKATHON_KEYWORDS & words)


def scrape(cfg) -> list[dict]:
    """
    Monitor all watchlist URLs from config.yaml for changes.
    Returns hackathon-like items for pages that changed AND contain
    hackathon keywords.
    """
    urls: list[str] = cfg.watchlist_urls or []
    if not urls:
        log.info("[watchlist] No URLs configured.")
        return []

    hashes = _load_hashes()
    discoveries: list[dict] = []

    for url in urls:
        log.info("[watchlist] Checking: %s", url)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()

            text = _extract_text(resp.text)
            current_hash = _page_hash(text)
            previous_hash = hashes.get(url)

            if previous_hash is None:
                # First time seeing this URL — record hash, don't alert
                log.info("[watchlist] First visit, recording baseline: %s", url)
                hashes[url] = current_hash

            elif current_hash != previous_hash:
                log.info("[watchlist] Change detected: %s", url)
                hashes[url] = current_hash

                if _has_hackathon_keywords(text):
                    # Extract a snippet around the first hackathon keyword mention
                    snippet = _extract_snippet(text)
                    discoveries.append({
                        "name": f"Page change detected: {url}",
                        "url": url,
                        "description": snippet,
                        "deadline": "",
                        "source": "watchlist",
                    })
                else:
                    log.info("[watchlist] Changed but no hackathon keywords: %s", url)
            else:
                log.info("[watchlist] No change: %s", url)

        except Exception as exc:
            log.error("[watchlist] Failed to check %s: %s", url, exc)

        time.sleep(_REQUEST_DELAY)

    _save_hashes(hashes)
    log.info("[watchlist] Discoveries: %d", len(discoveries))
    return discoveries


def _extract_snippet(text: str, window: int = 300) -> str:
    """
    Find the first occurrence of a hackathon keyword and return a snippet
    of surrounding text for the notification body.
    """
    for kw in _HACKATHON_KEYWORDS:
        idx = text.find(kw)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(text), idx + window)
            return "..." + text[start:end].strip() + "..."
    return text[:window]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    class _MockCfg:
        scrapers = {"watchlist": {"enabled": True}}
        watchlist_urls = [
            "https://devpost.com/hackathons",
        ]

    results = scrape(_MockCfg())
    for h in results:
        print(f"  [watchlist] {h['name']}")
    print(f"\nDiscoveries: {len(results)}")
