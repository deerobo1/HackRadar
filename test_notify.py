"""
test_notify.py — Phase 4 live test for HackRadar Telegram notifier
====================================================================
Sends a real test notification to your Telegram chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file.

Run:
    .\.venv\Scripts\python.exe test_notify.py
"""

import logging
import sys
import os

# Auto-load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Force UTF-8 output so emoji print correctly on Windows
sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def main():
    # Check credentials
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or bot_token == "PASTE_YOUR_BOT_TOKEN":
        print("\n[FAIL] TELEGRAM_BOT_TOKEN is not set in your .env file.")
        print("       Get one from @BotFather on Telegram -> /newbot")
        sys.exit(1)

    if not chat_id or chat_id == "PASTE_YOUR_CHAT_ID":
        print("\n[FAIL] TELEGRAM_CHAT_ID is not set in your .env file.")
        print(f"       Visit: https://api.telegram.org/bot{bot_token}/getUpdates")
        sys.exit(1)

    print(f"\n[OK] Bot token : ...{bot_token[-10:]}")
    print(f"[OK] Chat ID   : {chat_id}")

    # ── 2. Build mock objects ─────────────────────────────────────────────
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        notification_channel="telegram",
        snooze_duration_hours=48,
        telegram=SimpleNamespace(bot_token=bot_token, chat_id=chat_id),
    )

    # Fake DB — just enough for the notifier to work
    class FakeDB:
        def set_reminder(self, *a, **kw): pass
        def get_all_seen(self): return []
        def close(self): pass

    db = FakeDB()

    # ── 3. Sample hackathon ───────────────────────────────────────────────
    test_hackathon = {
        "name": "🧪 HackRadar Test Notification",
        "url": "https://github.com/deerobo1/HackRadar",
        "description": (
            "This is a test notification from HackRadar. "
            "If you can see this, Phase 4 is working correctly! "
            "The Register / Snooze / Dismiss buttons below are fully functional."
        ),
        "deadline": "Jul 01, 2026",
        "source": "test",
        "prize": "$50,000",
        "filter_reason": "This is a test hackathon to verify HackRadar notifications work end-to-end.",
        "filter_tags": ["test", "AI", "open-source", "online"],
    }

    # ── 4. Send via dispatcher ────────────────────────────────────────────
    print("\n📤  Sending test notification to Telegram...")

    from notifier.telegram import send_telegram_notification
    send_telegram_notification(test_hackathon, cfg, db)

    print("\n🎉  Done! Check your Telegram — a message should have arrived.")
    print("    - Tap ✅ Register  → opens the HackRadar GitHub page")
    print("    - Tap ⏰ Snooze   → bot will acknowledge (no actual snooze in test mode)")
    print("    - Tap ❌ Dismiss  → bot will acknowledge\n")


if __name__ == "__main__":
    main()
