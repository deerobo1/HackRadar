"""
notifier/__init__.py — HackRadar notification dispatcher
=========================================================
Reads notification_channel from config and routes to the correct backend.

Usage:
    from notifier import dispatch
    dispatch(hackathon, cfg, db)
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config
    from db import DB

log = logging.getLogger(__name__)


def dispatch(hackathon: dict, cfg: "Config", db: "DB") -> None:
    """
    Route a matched hackathon to the configured notification channel.

    Args:
        hackathon : dict with keys name, url, description, deadline,
                    filter_reason, filter_tags
        cfg       : loaded Config object
        db        : open DB instance (for action callbacks)
    """
    channel = cfg.notification_channel.lower()

    if channel == "telegram":
        from notifier.telegram import send_telegram_notification
        send_telegram_notification(hackathon, cfg, db)

    elif channel == "whatsapp":
        from notifier.whatsapp import send_whatsapp_notification
        send_whatsapp_notification(hackathon, cfg, db)

    else:
        log.error(
            "Unknown notification channel: '%s'. "
            "Set notification_channel to 'telegram' or 'whatsapp' in config.yaml.",
            channel,
        )
        raise ValueError(f"Unknown notification channel: {channel!r}")
