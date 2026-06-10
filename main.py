"""
main.py — HackRadar orchestrator
==================================
Entrypoint for the full pipeline:
  1. Load config
  2. Run all enabled scrapers
  3. Deduplicate against the DB (seen + dismissed)
  4. Pass new hackathons through the Gemini filter
  5. Send notifications for matched hackathons
  6. Mark everything as seen

Run:
    python main.py
"""

import logging
import sys
from datetime import datetime, timezone

from config_loader import load_config
from db import DB

# Scrapers
from scrapers.devpost import scrape as scrape_devpost
from scrapers.unstop import scrape as scrape_unstop
from scrapers.mlh import scrape as scrape_mlh
from scrapers.watchlist import scrape as scrape_watchlist
from scrapers.social import scrape as scrape_social
# LinkedIn is intentionally excluded from the CI pipeline.
# Run manually: python scrapers/linkedin.py

# Filter and notifier
from filter import filter_hackathons
from notifier import dispatch

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("hackradar.main")


# ---------------------------------------------------------------------------
# Scraper registry
# ---------------------------------------------------------------------------

SCRAPER_REGISTRY = {
    "devpost":   scrape_devpost,
    "unstop":    scrape_unstop,
    "mlh":       scrape_mlh,
    "watchlist": scrape_watchlist,
    "social":    scrape_social,
}


def run_scrapers(cfg) -> list[dict]:
    """
    Run all enabled scrapers and return a deduplicated list of hackathon dicts.
    Each scraper must return a list of dicts with keys:
        name, url, description, deadline, source
    """
    all_hackathons: list[dict] = []
    seen_urls: set[str] = set()

    for name, scraper_fn in SCRAPER_REGISTRY.items():
        scraper_cfg = cfg.scrapers.get(name, {})
        if not scraper_cfg.get("enabled", True):
            log.info("Scraper '%s' is disabled — skipping.", name)
            continue

        log.info("Running scraper: %s", name)
        try:
            results = scraper_fn(cfg)
            valid = [h for h in results if _validate_hackathon(h)]
            # Deduplicate within this run (same URL from multiple scrapers)
            for h in valid:
                if h["url"] not in seen_urls:
                    seen_urls.add(h["url"])
                    all_hackathons.append(h)
            log.info("Scraper '%s' returned %d valid hackathon(s).", name, len(valid))
        except Exception as exc:
            log.error("Scraper '%s' failed: %s", name, exc, exc_info=True)

    log.info("Total unique hackathons discovered: %d", len(all_hackathons))
    return all_hackathons


def _validate_hackathon(h: dict) -> bool:
    """Ensure the hackathon dict has the required fields."""
    required = ("name", "url")
    for field in required:
        if not h.get(field):
            log.warning("Hackathon missing required field '%s': %s", field, h)
            return False
    return True


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("HackRadar — starting run at %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    # 1. Load configuration
    cfg = load_config()

    # 2. Open DB
    db = DB(cfg.database_path)
    log.info("DB stats at start: %s", db.stats())

    # 3. Scrape all sources
    hackathons = run_scrapers(cfg)

    # 4. Filter out already seen or dismissed hackathons
    new_hackathons = []
    for h in hackathons:
        url = h["url"]
        if db.is_dismissed(url):
            log.debug("Skipping dismissed: %s", url)
        elif db.is_seen(url):
            log.debug("Skipping already seen: %s", url)
        else:
            new_hackathons.append(h)

    log.info(
        "%d hackathon(s) after dedup filter (dismissed/seen removed).",
        len(new_hackathons),
    )

    if not new_hackathons:
        log.info("Nothing new to process. Exiting.")
        db.close()
        return

    # 5. LLM filter — ask Gemini which ones match the user's profile
    log.info("Sending %d hackathon(s) to Gemini for relevance filtering...", len(new_hackathons))
    matched = filter_hackathons(new_hackathons, cfg)
    log.info("%d hackathon(s) matched the interest profile.", len(matched))

    # 6. Mark ALL new hackathons as seen (even non-matches — we don't want
    #    to re-run them through Claude on every cycle)
    for h in new_hackathons:
        db.mark_seen(h)

    # 7. Send notifications for matched hackathons
    if not matched:
        log.info("No matched hackathons to notify about.")
    else:
        for h in matched:
            log.info("Notifying: %s (%s)", h["name"], h.get("url"))
            try:
                dispatch(h, cfg, db)
            except Exception as exc:
                log.error("Failed to send notification for %s: %s", h["name"], exc, exc_info=True)

    # 8. Summary
    log.info("=" * 60)
    log.info("Run complete. DB stats: %s", db.stats())
    log.info("=" * 60)
    db.close()


if __name__ == "__main__":
    main()
