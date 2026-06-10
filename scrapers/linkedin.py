"""
scrapers/linkedin.py — LinkedIn hackathon scraper
===================================================
⚠️  IMPORTANT: This scraper is designed to run LOCALLY only.
LinkedIn aggressively blocks GitHub Actions IP ranges. Do NOT include this
in any CI/CD pipeline. Run it manually on your local machine or via a
residential proxy.

Setup:
  1. Create a dedicated throwaway LinkedIn account (not your main account).
  2. Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD environment variables.
  3. Optionally set a residential proxy in config.yaml (linkedin.proxy).

Run:
    python scrapers/linkedin.py

Common HackRadar output format:
    {
        "name":        str,
        "url":         str,
        "description": str,
        "deadline":    "",
        "source":      "linkedin",
    }
"""

import logging
import os
import time
import re
from typing import Optional

log = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from webdriver_manager.chrome import ChromeDriverManager
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False
    log.warning(
        "selenium / webdriver-manager not installed. "
        "LinkedIn scraper will be disabled. "
        "Run: pip install selenium webdriver-manager"
    )

_HACKATHON_KEYWORDS = re.compile(
    r"\b(hackathon|hack\s*day|build\s*challenge|code\s*jam|dev\s*challenge|"
    r"open\s*innovation|ideathon)\b",
    re.IGNORECASE,
)
_LINKEDIN_BASE = "https://www.linkedin.com"
_LOGIN_URL = f"{_LINKEDIN_BASE}/login"
_HASHTAG_BASE = f"{_LINKEDIN_BASE}/feed/hashtag"
_COMPANY_BASE = f"{_LINKEDIN_BASE}/company"


def scrape(cfg) -> list[dict]:
    """Entry point called by main.py (when linkedin.enabled = true in config)."""
    if not _SELENIUM_AVAILABLE:
        log.error("[linkedin] Selenium not installed — skipping.")
        return []

    li_cfg = cfg.linkedin
    email = li_cfg.email or os.environ.get("LINKEDIN_EMAIL", "")
    password = li_cfg.password or os.environ.get("LINKEDIN_PASSWORD", "")

    if not email or not password:
        log.error(
            "[linkedin] Credentials not configured. "
            "Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD environment variables."
        )
        return []

    max_posts = int(cfg.scrapers.get("linkedin", {}).get("max_posts", 30))
    driver = None

    try:
        driver = _create_driver(li_cfg)
        _login(driver, email, password)
        time.sleep(3)  # Let the feed load

        discovered: list[dict] = []
        seen_urls: set[str] = set()

        # Scrape hashtag feeds
        for hashtag in li_cfg.hashtags:
            log.info("[linkedin] Scraping hashtag: #%s", hashtag)
            try:
                posts = _scrape_hashtag(driver, hashtag, max_posts)
                for post in posts:
                    if post["url"] not in seen_urls:
                        seen_urls.add(post["url"])
                        discovered.append(post)
            except Exception as exc:
                log.warning("[linkedin] Hashtag #%s failed: %s", hashtag, exc)
            time.sleep(2)

        # Scrape company pages
        for company in li_cfg.company_pages:
            log.info("[linkedin] Scraping company: %s", company)
            try:
                posts = _scrape_company_posts(driver, company, max_posts)
                for post in posts:
                    if post["url"] not in seen_urls:
                        seen_urls.add(post["url"])
                        discovered.append(post)
            except Exception as exc:
                log.warning("[linkedin] Company %s failed: %s", company, exc)
            time.sleep(2)

        log.info("[linkedin] Total discovered: %d", len(discovered))
        return discovered

    except Exception as exc:
        log.error("[linkedin] Fatal error: %s", exc, exc_info=True)
        return []

    finally:
        if driver:
            driver.quit()


def _create_driver(li_cfg) -> "webdriver.Chrome":
    """Create a headless Chrome WebDriver."""
    options = Options()

    if li_cfg.headless:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    if li_cfg.proxy:
        options.add_argument(f"--proxy-server={li_cfg.proxy}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Patch navigator.webdriver to avoid LinkedIn bot detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    return driver


def _login(driver: "webdriver.Chrome", email: str, password: str) -> None:
    """Log in to LinkedIn with the given credentials."""
    log.info("[linkedin] Logging in as %s", email)
    driver.get(_LOGIN_URL)

    wait = WebDriverWait(driver, 15)

    # Fill email
    email_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
    email_field.clear()
    email_field.send_keys(email)
    time.sleep(0.5)

    # Fill password
    pass_field = driver.find_element(By.ID, "password")
    pass_field.clear()
    pass_field.send_keys(password)
    time.sleep(0.5)

    # Submit
    pass_field.submit()

    # Wait for feed to load
    try:
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "feed-identity-module")))
        log.info("[linkedin] Login successful.")
    except TimeoutException:
        # Sometimes the feed takes a different path; check we're not on login page
        if "login" in driver.current_url or "checkpoint" in driver.current_url:
            raise RuntimeError(
                "LinkedIn login failed. Check credentials or solve CAPTCHA manually."
            )
        log.info("[linkedin] Login appears successful (alternative page layout).")


def _scrape_hashtag(driver: "webdriver.Chrome", hashtag: str, max_posts: int) -> list[dict]:
    """Scrape LinkedIn posts for a given hashtag."""
    url = f"{_HASHTAG_BASE}/?hashtag={hashtag}"
    driver.get(url)
    time.sleep(3)

    return _extract_posts(driver, f"#{hashtag}", max_posts)


def _scrape_company_posts(
    driver: "webdriver.Chrome", company_slug: str, max_posts: int
) -> list[dict]:
    """Scrape a LinkedIn company page's recent posts."""
    url = f"{_COMPANY_BASE}/{company_slug}/posts/"
    driver.get(url)
    time.sleep(3)

    return _extract_posts(driver, company_slug, max_posts)


def _extract_posts(
    driver: "webdriver.Chrome", context: str, max_posts: int
) -> list[dict]:
    """
    Extract post text + links from the current LinkedIn page.
    Scrolls to load more posts up to max_posts.
    """
    posts: list[dict] = []
    scroll_attempts = 0
    max_scrolls = 5

    while len(posts) < max_posts and scroll_attempts < max_scrolls:
        # LinkedIn post containers change class names frequently
        post_elements = driver.find_elements(
            By.CSS_SELECTOR,
            "div.feed-shared-update-v2, article.feed-shared-update-v2, "
            ".occludable-update, [data-urn]",
        )

        for el in post_elements:
            if len(posts) >= max_posts:
                break
            post = _parse_post_element(el, context)
            if post and post["url"] not in {p["url"] for p in posts}:
                posts.append(post)

        # Scroll down for more
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        scroll_attempts += 1

    return posts


def _parse_post_element(el, context: str) -> Optional[dict]:
    """Extract text and URL from a LinkedIn post element."""
    try:
        # Get post text
        text_el = el.find_element(
            By.CSS_SELECTOR,
            ".feed-shared-text, .feed-shared-update-v2__description, "
            ".update-components-text",
        )
        text = text_el.text.strip()
    except NoSuchElementException:
        return None

    if not text or not _HACKATHON_KEYWORDS.search(text):
        return None

    # Try to get the post permalink
    try:
        link_el = el.find_element(
            By.CSS_SELECTOR,
            "a[href*='/posts/'], a[href*='/feed/update/']",
        )
        url = link_el.get_attribute("href") or ""
        # Normalise LinkedIn URLs
        if "linkedin.com" not in url:
            url = _LINKEDIN_BASE + url
    except NoSuchElementException:
        # Use a synthetic URL from context
        urn = el.get_attribute("data-urn") or ""
        url = f"{_LINKEDIN_BASE}/feed/update/{urn}" if urn else ""

    if not url:
        return None

    name = text[:100] + ("…" if len(text) > 100 else "")

    return {
        "name": name,
        "url": url,
        "description": text[:500],
        "deadline": "",
        "source": "linkedin",
    }


if __name__ == "__main__":
    """
    Run the LinkedIn scraper standalone.
    Results are printed to stdout and also written to linkedin_results.json.
    """
    import json
    import sys
    from config_loader import load_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    cfg = load_config()

    if not cfg.scrapers.get("linkedin", {}).get("enabled", False):
        log.warning(
            "LinkedIn scraper is disabled in config.yaml "
            "(scrapers.linkedin.enabled = false). "
            "Overriding for standalone run."
        )

    results = scrape(cfg)

    output_file = "linkedin_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Found {len(results)} hackathon posts → saved to {output_file}")
    for h in results:
        print(f"  • {h['name'][:70]}")
