"""
notifier/telegram.py — Telegram notification sender
=====================================================
Sends rich Telegram messages with inline keyboards for three actions:
  ✅ Register   — opens hackathon URL, sets deadline reminder
  ⏰ Snooze     — re-fires after configured snooze duration
  ❌ Dismiss    — permanently skips

Uses python-telegram-bot (v21+) in an async context but exposed via a
synchronous wrapper so main.py doesn't need to manage event loops.

Callback query data format:
  "register:<url>"
  "snooze:<url>"
  "dismiss:<url>"
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config_loader import Config
    from db import DB


def _format_message(hackathon: dict) -> str:
    """Build the Telegram message text."""
    name = hackathon.get("name", "Unknown Hackathon")
    url = hackathon.get("url", "")
    description = hackathon.get("description", "")[:300]
    deadline = hackathon.get("deadline", "")
    reason = hackathon.get("filter_reason", "")
    tags = hackathon.get("filter_tags", [])

    # Handle reminder notifications differently
    if hackathon.get("is_reminder"):
        return (
            f"⏰ *Deadline Reminder*\n\n"
            f"*{_escape_md(name)}*\n"
            f"📅 Deadline: {_escape_md(deadline)}\n\n"
            f"Don't forget to submit\\! 🚀\n"
            f"[Open Hackathon]({url})"
        )

    tags_str = " ".join(f"`{t}`" for t in tags) if tags else ""

    msg = f"🚀 *New Hackathon Alert\\!*\n\n"
    msg += f"*{_escape_md(name)}*\n\n"

    if description:
        msg += f"📝 {_escape_md(description)}\n\n"

    if deadline:
        msg += f"📅 Deadline: {_escape_md(deadline)}\n"

    if reason:
        msg += f"🤖 Why it matches: _{_escape_md(reason)}_\n"

    if tags_str:
        msg += f"\n🏷️ {tags_str}\n"

    msg += f"\n🔗 [View Hackathon]({url})"

    return msg


def _escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


async def _send_async(hackathon: dict, cfg: "Config", db: "DB") -> None:
    """Async implementation of the Telegram send."""
    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.constants import ParseMode
    except ImportError:
        log.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
        return

    bot_token = cfg.telegram.bot_token
    chat_id = cfg.telegram.chat_id

    if not bot_token or bot_token.startswith("${"):
        log.error("[telegram] bot_token is not configured.")
        return
    if not chat_id or chat_id.startswith("${"):
        log.error("[telegram] chat_id is not configured.")
        return

    url = hackathon.get("url", "")
    is_reminder = hackathon.get("is_reminder", False)

    # Build inline keyboard
    if is_reminder:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Open Hackathon", url=url)],
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Register", url=url),
                InlineKeyboardButton("⏰ Snooze", callback_data=f"snooze:{url}"),
                InlineKeyboardButton("❌ Dismiss", callback_data=f"dismiss:{url}"),
            ]
        ])

    message_text = _format_message(hackathon)

    bot = Bot(token=bot_token)
    async with bot:
        await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )

    log.info("[telegram] Sent notification for: %s", hackathon.get("name"))

    # Auto-set a deadline reminder if deadline is parseable and not a snooze re-fire
    if not is_reminder and hackathon.get("deadline"):
        _try_set_reminder(hackathon, db, cfg)


def _try_set_reminder(hackathon: dict, db: "DB", cfg: "Config") -> None:
    """
    Attempt to parse the deadline and set a reminder 2 days before it.
    Silently skips if the date is unparseable or already past.
    """
    from dateutil import parser as dateutil_parser

    deadline_str = hackathon.get("deadline", "")
    if not deadline_str:
        return

    try:
        deadline_dt = dateutil_parser.parse(deadline_str, fuzzy=True)
        # Make timezone-aware
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)

        remind_at = deadline_dt - timedelta(days=2)
        now = datetime.now(timezone.utc)

        if remind_at > now:
            db.set_reminder(hackathon, remind_at)
            log.info(
                "[telegram] Reminder set for %s at %s",
                hackathon.get("name"),
                remind_at.isoformat(),
            )
        else:
            log.info(
                "[telegram] Deadline too close for reminder: %s", deadline_str
            )
    except Exception as exc:
        log.debug("[telegram] Could not parse deadline '%s': %s", deadline_str, exc)


def send_telegram_notification(hackathon: dict, cfg: "Config", db: "DB") -> None:
    """
    Synchronous wrapper around the async Telegram sender.
    Called by notifier/__init__.py dispatch().
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in async context (e.g. telegram bot webhook handler)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, _send_async(hackathon, cfg, db)).result()
        else:
            loop.run_until_complete(_send_async(hackathon, cfg, db))
    except RuntimeError:
        # No event loop — create one
        asyncio.run(_send_async(hackathon, cfg, db))


# ---------------------------------------------------------------------------
# Callback query handler (used when running the bot in polling mode)
# ---------------------------------------------------------------------------

async def handle_callback_query(update, context):
    """
    Handle inline keyboard button presses.
    Register this with python-telegram-bot Application if running a long-lived bot.

    Callback data format:
        "snooze:<url>"
        "dismiss:<url>"
    """
    from db import DB
    from config_loader import load_config

    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data:
        return

    cfg = load_config()
    db = DB(cfg.database_path)

    if data.startswith("snooze:"):
        url = data[len("snooze:"):]
        # We need the hackathon name — read from DB seen table
        seen_records = db.get_all_seen()
        name = next((r["name"] for r in seen_records if r["url"] == url), url)
        snooze_until = datetime.now(timezone.utc) + timedelta(hours=cfg.snooze_duration_hours)
        db.snooze({"url": url, "name": name}, snooze_until)
        await query.edit_message_text(
            f"⏰ Snoozed\\! I'll remind you again in {cfg.snooze_duration_hours} hours\\.",
            parse_mode="MarkdownV2",
        )
        log.info("[telegram] Snoozed: %s until %s", url, snooze_until.isoformat())

    elif data.startswith("dismiss:"):
        url = data[len("dismiss:"):]
        seen_records = db.get_all_seen()
        name = next((r["name"] for r in seen_records if r["url"] == url), url)
        db.dismiss(url, name)
        await query.edit_message_text("❌ Dismissed\\. Won't notify you about this again\\.")
        log.info("[telegram] Dismissed: %s", url)

    db.close()
