"""
test_scrapers.py — Live integration test for all HackRadar scrapers
=====================================================================
Run:  python test_scrapers.py
"""
import sys, io, json, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from scrapers.devpost import scrape as devpost_scrape
from scrapers.unstop import scrape as unstop_scrape
from scrapers.mlh import scrape as mlh_scrape
from scrapers.social import scrape as social_scrape
from scrapers.watchlist import scrape as watchlist_scrape

RESULTS = {}

class MockCfg:
    scrapers = {
        "devpost":   {"max_pages": 1},
        "unstop":    {"max_pages": 1},
        "mlh":       {},
        "social":    {"max_items_per_feed": 5},
        "watchlist": {"enabled": True},
    }
    watchlist_urls = [
        "https://devpost.com/hackathons",
        "https://mlh.io/seasons/2026/events",
    ]
    nitter_rss_feeds = []

cfg = MockCfg()

def run_scraper(name, fn):
    print(f"\n{'='*60}")
    print(f"SCRAPER: {name}")
    print("="*60)
    try:
        results = fn(cfg)
        RESULTS[name] = {"status": "OK", "count": len(results)}
        print(f"  -> {len(results)} results")
        for h in results[:3]:
            print(f"     name    : {h.get('name','')[:60]}")
            print(f"     url     : {h.get('url','')[:70]}")
            print(f"     deadline: {h.get('deadline','')}")
            print(f"     source  : {h.get('source','')}")
            print()
    except Exception as exc:
        RESULTS[name] = {"status": "ERROR", "error": str(exc)}
        print(f"  -> ERROR: {exc}")

run_scraper("devpost",   devpost_scrape)
run_scraper("unstop",    unstop_scrape)
run_scraper("mlh",       mlh_scrape)
run_scraper("social",    social_scrape)
run_scraper("watchlist", watchlist_scrape)

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
for name, res in RESULTS.items():
    status = res["status"]
    count  = res.get("count", 0)
    err    = res.get("error", "")
    print(f"  {name:<12} {status}  {('count='+str(count)) if status=='OK' else err}")
