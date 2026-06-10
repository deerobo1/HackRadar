"""
snooze.py — HackRadar snooze re-fire job
==========================================
Queries the DB for:
  1. Snoozed hackathons whose fire_at time has passed → re-dispatch
  2. Pending deadline reminders whose remind_at has passed → re-dispatch

Designed to run on a cron schedule (every 15 minutes via GitHub Actions).

Run:
    python snooze.py
"""

import logging
import sys
from datetime import datetime, timezone

from config_loader import load_config
from db import DB
from notifier import dispatch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hackradar.snooze")


def run_snooze_check():
    log.info("Snooze check — %s", datetime.now(timezone.utc).isoformat())

    cfg = load_config()
    db = DB(cfg.database_path)

    # ── 1. Re-fire due snoozed hackathons ────────────────────────────────────
    due_snoozed = db.get_due_snoozed()
    log.info("Due snoozed items: %d", len(due_snoozed))

    for record in due_snoozed:
        hackathon = record["data"]
        url = record["url"]
        log.info("Re-firing snoozed hackathon: %s", hackathon.get("name"))
        try:
            dispatch(hackathon, cfg, db)
            db.delete_snoozed(url)
        except Exception as exc:
            log.error("Failed to re-fire snooze for %s: %s", url, exc, exc_info=True)

    # ── 2. Fire due deadline reminders ───────────────────────────────────────
    due_reminders = db.get_due_reminders()
    log.info("Due reminders: %d", len(due_reminders))

    for reminder in due_reminders:
        url = reminder["url"]
        name = reminder["name"]
        deadline = reminder.get("deadline", "soon")
        log.info("Firing deadline reminder: %s (deadline: %s)", name, deadline)

        # Build a synthetic hackathon dict for the reminder notification
        reminder_hackathon = {
            "url": url,
            "name": name,
            "description": f"⏰ Reminder: Deadline approaching on {deadline}",
            "deadline": deadline,
            "source": "reminder",
            "filter_reason": "Deadline reminder you set when you registered.",
            "filter_tags": ["reminder"],
            "is_reminder": True,
        }

        try:
            dispatch(reminder_hackathon, cfg, db)
            db.mark_reminder_fired(url)
        except Exception as exc:
            log.error(
                "Failed to fire reminder for %s: %s", url, exc, exc_info=True
            )

    log.info("Snooze check complete.")
    db.close()


if __name__ == "__main__":
    run_snooze_check()
