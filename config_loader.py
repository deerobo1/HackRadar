"""
config_loader.py — HackRadar configuration loader
===================================================
Reads config.yaml and resolves ${ENV_VAR} placeholders from environment
variables. Provides a typed Config dataclass for the rest of the app.

Usage:
    from config_loader import load_config
    cfg = load_config()
    print(cfg.notification_channel)
"""

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value):
    """
    Recursively walk a parsed YAML structure and replace ${VAR} placeholders
    with the corresponding environment variable value.
    Raises ValueError if a required variable is not set.
    """
    if isinstance(value, str):
        def replacer(match):
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                log.warning(
                    "Environment variable '%s' is not set — leaving placeholder.", var_name
                )
                return match.group(0)  # Keep original placeholder
            return env_val

        return _ENV_VAR_PATTERN.sub(replacer, value)

    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]

    return value


# ---------------------------------------------------------------------------
# Typed config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class WhatsAppConfig:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    to_number: str = ""
    webhook_url: str = ""


@dataclass
class AnthropicConfig:
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 512


@dataclass
class LinkedInConfig:
    email: str = ""
    password: str = ""
    hashtags: list = field(default_factory=list)
    company_pages: list = field(default_factory=list)
    headless: bool = True
    proxy: str = ""


@dataclass
class ScraperSettings:
    enabled: bool = True
    max_pages: int = 3
    base_url: str = ""
    max_items_per_feed: int = 20


@dataclass
class Config:
    interest_profile: str = ""
    notification_channel: str = "telegram"    # "telegram" | "whatsapp"
    snooze_duration_hours: int = 48
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    linkedin: LinkedInConfig = field(default_factory=LinkedInConfig)
    watchlist_urls: list = field(default_factory=list)
    nitter_rss_feeds: list = field(default_factory=list)
    database_path: str = "hackradar.db"
    scrapers: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_path: str = "config.yaml") -> Config:
    """
    Load and validate HackRadar configuration from a YAML file.
    Environment variable placeholders (${VAR}) are resolved automatically.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path.resolve()}\n"
            "Copy config.yaml.example to config.yaml and fill in your details."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError("config.yaml is empty.")

    # Resolve ${ENV_VAR} placeholders throughout the entire structure
    data = _resolve_env_vars(raw)

    # Build typed Config
    cfg = Config()
    cfg.interest_profile = data.get("interest_profile", "").strip()
    cfg.notification_channel = data.get("notification_channel", "telegram").lower()
    cfg.snooze_duration_hours = int(data.get("snooze_duration_hours", 48))
    cfg.watchlist_urls = data.get("watchlist_urls", [])
    cfg.nitter_rss_feeds = data.get("nitter_rss_feeds", [])

    # Telegram
    tg_raw = data.get("telegram", {})
    cfg.telegram = TelegramConfig(
        bot_token=tg_raw.get("bot_token", ""),
        chat_id=str(tg_raw.get("chat_id", "")),
    )

    # WhatsApp
    wa_raw = data.get("whatsapp", {})
    cfg.whatsapp = WhatsAppConfig(
        account_sid=wa_raw.get("account_sid", ""),
        auth_token=wa_raw.get("auth_token", ""),
        from_number=wa_raw.get("from_number", ""),
        to_number=wa_raw.get("to_number", ""),
        webhook_url=wa_raw.get("webhook_url", ""),
    )

    # Anthropic
    anth_raw = data.get("anthropic", {})
    cfg.anthropic = AnthropicConfig(
        model=anth_raw.get("model", "claude-sonnet-4-5"),
        max_tokens=int(anth_raw.get("max_tokens", 512)),
    )

    # LinkedIn
    li_raw = data.get("linkedin", {})
    cfg.linkedin = LinkedInConfig(
        email=li_raw.get("email", ""),
        password=li_raw.get("password", ""),
        hashtags=li_raw.get("hashtags", []),
        company_pages=li_raw.get("company_pages", []),
        headless=li_raw.get("headless", True),
        proxy=li_raw.get("proxy", ""),
    )

    # Database
    db_raw = data.get("database", {})
    cfg.database_path = db_raw.get("path", "hackradar.db")

    # Scrapers (pass raw dict through — individual scrapers read what they need)
    cfg.scrapers = data.get("scrapers", {})

    _validate_config(cfg)
    log.info("Config loaded from %s (channel: %s)", path, cfg.notification_channel)
    return cfg


def _validate_config(cfg: Config) -> None:
    """Warn about missing or obviously invalid configuration values."""
    if not cfg.interest_profile:
        log.warning("interest_profile is empty — Claude will have no filter criteria.")

    if cfg.notification_channel not in ("telegram", "whatsapp"):
        raise ValueError(
            f"notification_channel must be 'telegram' or 'whatsapp', "
            f"got: '{cfg.notification_channel}'"
        )

    if cfg.notification_channel == "telegram":
        if not cfg.telegram.bot_token or cfg.telegram.bot_token.startswith("${"):
            log.warning("Telegram bot_token is not configured.")
        if not cfg.telegram.chat_id or cfg.telegram.chat_id.startswith("${"):
            log.warning("Telegram chat_id is not configured.")

    if cfg.notification_channel == "whatsapp":
        if not cfg.whatsapp.account_sid or cfg.whatsapp.account_sid.startswith("${"):
            log.warning("Twilio account_sid is not configured.")
        if not cfg.whatsapp.auth_token or cfg.whatsapp.auth_token.startswith("${"):
            log.warning("Twilio auth_token is not configured.")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "The LLM filter will not work."
        )


# ---------------------------------------------------------------------------
# CLI quick-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    print("[OK] Config loaded:")
    print(f"   Channel      : {cfg.notification_channel}")
    print(f"   Model        : {cfg.anthropic.model}")
    print(f"   DB path      : {cfg.database_path}")
    print(f"   Watchlist    : {len(cfg.watchlist_urls)} URLs")
    print(f"   RSS feeds    : {len(cfg.nitter_rss_feeds)} feeds")
    print(f"   Interest     : {cfg.interest_profile[:80]}...")
