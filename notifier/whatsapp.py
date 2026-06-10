"""
notifier/whatsapp.py — WhatsApp notification sender (via Twilio)
================================================================
Sends WhatsApp messages via the Twilio API.

Since WhatsApp doesn't support inline buttons, actions are triggered via
reply keywords:
  R → Register (opens link + sets reminder)
  S → Snooze   (re-fires after snooze duration)
  D → Dismiss  (permanently skip)

To handle incoming replies, expose a webhook endpoint using a web framework
(e.g. Flask) or a Twilio Studio flow. The handle_incoming_reply() function
below processes the parsed reply payload.

Twilio WhatsApp sandbox setup:
  1. Sign up at twilio.com and activate the WhatsApp sandbox.
  2. Send "join <your-sandbox-word>" from your WhatsApp to the sandbox number.
  3. Configure the Twilio webhook URL to point to your server or ngrok endpoint.
  4. See README.md for full instructions.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config_loader import Config
    from db import DB


def _format_message(hackathon: dict) -> str:
    """Build the WhatsApp message text."""
    name = hackathon.get("name", "Unknown Hackathon")
    url = hackathon.get("url", "")
    description = hackathon.get("description", "")[:250]
    deadline = hackathon.get("deadline", "")
    reason = hackathon.get("filter_reason", "")
    tags = hackathon.get("filter_tags", [])

    if hackathon.get("is_reminder"):
        return (
            f"⏰ *Deadline Reminder*\n\n"
            f"*{name}*\n"
            f"📅 Deadline: {deadline}\n\n"
            f"Don't forget to submit! 🚀\n"
            f"🔗 {url}"
        )

    parts = [
        f"🚀 *New Hackathon Alert!*\n",
        f"*{name}*",
    ]

    if description:
        parts.append(f"\n📝 {description}")

    if deadline:
        parts.append(f"\n📅 Deadline: {deadline}")

    if reason:
        parts.append(f"\n🤖 Match: _{reason}_")

    if tags:
        parts.append(f"\n🏷️ {', '.join(tags)}")

    parts.append(f"\n\n🔗 {url}")
    parts.append(
        f"\n\n💬 Reply with:\n"
        f"  *R* — Register (+ set reminder)\n"
        f"  *S* — Snooze {48}h\n"
        f"  *D* — Dismiss forever"
    )

    return "\n".join(parts)


def send_whatsapp_notification(hackathon: dict, cfg: "Config", db: "DB") -> None:
    """
    Send a WhatsApp message via Twilio.

    Args:
        hackathon : hackathon dict
        cfg       : loaded Config
        db        : open DB instance
    """
    try:
        from twilio.rest import Client
    except ImportError:
        log.error("twilio not installed. Run: pip install twilio")
        return

    account_sid = cfg.whatsapp.account_sid
    auth_token = cfg.whatsapp.auth_token
    from_number = cfg.whatsapp.from_number
    to_number = cfg.whatsapp.to_number

    if not account_sid or account_sid.startswith("${"):
        log.error("[whatsapp] Twilio account_sid is not configured.")
        return
    if not auth_token or auth_token.startswith("${"):
        log.error("[whatsapp] Twilio auth_token is not configured.")
        return
    if not from_number or not to_number:
        log.error("[whatsapp] from_number or to_number is not configured.")
        return

    message_body = _format_message(hackathon)

    try:
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=message_body,
            from_=from_number,
            to=to_number,
        )
        log.info(
            "[whatsapp] Message sent. SID: %s | Status: %s",
            message.sid,
            message.status,
        )
    except Exception as exc:
        log.error("[whatsapp] Failed to send message: %s", exc, exc_info=True)
        raise


def handle_incoming_reply(
    from_number: str,
    body: str,
    cfg: "Config",
    db: "DB",
) -> str:
    """
    Process an incoming WhatsApp reply from the user.

    Called by your webhook handler when Twilio delivers an inbound message.
    Returns a response string to send back to the user.

    Args:
        from_number : the user's WhatsApp number (Twilio format)
        body        : the message text the user sent
        cfg         : loaded Config
        db          : open DB instance

    Returns:
        str: Response message to send back to the user
    """
    keyword = body.strip().upper()

    if keyword == "R":
        return _handle_register(db, cfg)
    elif keyword == "S":
        return _handle_snooze(db, cfg)
    elif keyword == "D":
        return _handle_dismiss(db, cfg)
    else:
        return (
            "🤖 HackRadar here! Reply with:\n"
            "  *R* — Register\n"
            "  *S* — Snooze\n"
            "  *D* — Dismiss\n\n"
            "_(Reply to a specific hackathon notification to take action on it.)_"
        )


def _handle_register(db: "DB", cfg: "Config") -> str:
    """
    Handle 'R' reply.
    Since we can't track which hackathon the user is replying to via WhatsApp
    without a conversation state store, we return the most recently notified
    hackathon from the seen table.
    """
    seen = db.get_all_seen()
    if not seen:
        return "No recent hackathons found."

    latest = seen[0]  # Most recently seen (DB orders by first_seen_at DESC)
    hackathon = {"url": latest["url"], "name": latest["name"], "deadline": ""}

    # Try to set a reminder
    try:
        remind_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.set_reminder(hackathon, remind_at)
        return (
            f"✅ Registered! Opening: {latest['url']}\n\n"
            f"⏰ I'll remind you in 7 days. "
            f"Reply *S* to snooze or *D* to dismiss."
        )
    except Exception as exc:
        log.error("[whatsapp] Failed to set reminder: %s", exc)
        return f"✅ Registered! {latest['url']}"


def _handle_snooze(db: "DB", cfg: "Config") -> str:
    """Handle 'S' reply — snooze the most recently seen hackathon."""
    seen = db.get_all_seen()
    if not seen:
        return "No recent hackathons to snooze."

    latest = seen[0]
    snooze_until = datetime.now(timezone.utc) + timedelta(hours=cfg.snooze_duration_hours)
    hackathon = {"url": latest["url"], "name": latest["name"]}

    try:
        db.snooze(hackathon, snooze_until)
        return (
            f"⏰ Snoozed! I'll remind you about *{latest['name']}* "
            f"in {cfg.snooze_duration_hours} hours."
        )
    except Exception as exc:
        log.error("[whatsapp] Failed to snooze: %s", exc)
        return "Failed to snooze. Please try again."


def _handle_dismiss(db: "DB", cfg: "Config") -> str:
    """Handle 'D' reply — permanently dismiss the most recently seen hackathon."""
    seen = db.get_all_seen()
    if not seen:
        return "No recent hackathons to dismiss."

    latest = seen[0]
    try:
        db.dismiss(latest["url"], latest["name"])
        return f"❌ Dismissed *{latest['name']}*. Won't notify you about this again."
    except Exception as exc:
        log.error("[whatsapp] Failed to dismiss: %s", exc)
        return "Failed to dismiss. Please try again."


# ---------------------------------------------------------------------------
# Example Flask webhook handler (optional, for reference)
# ---------------------------------------------------------------------------

def make_flask_webhook_app(cfg: "Config", db: "DB"):
    """
    Create a minimal Flask app to receive Twilio WhatsApp webhooks.
    
    Usage:
        app = make_flask_webhook_app(cfg, db)
        app.run(port=5000)
    
    Then expose port 5000 via ngrok:
        ngrok http 5000
    
    Set the ngrok URL as your Twilio webhook in the Twilio console.
    """
    try:
        from flask import Flask, request
        from twilio.twiml.messaging_response import MessagingResponse
    except ImportError:
        log.error("Flask or twilio not installed. Run: pip install flask twilio")
        return None

    app = Flask("hackradar_webhook")

    @app.route("/whatsapp/webhook", methods=["POST"])
    def webhook():
        from_number = request.form.get("From", "")
        body = request.form.get("Body", "")
        log.info("[whatsapp webhook] From: %s | Body: %s", from_number, body)

        response_text = handle_incoming_reply(from_number, body, cfg, db)

        resp = MessagingResponse()
        resp.message(response_text)
        return str(resp), 200, {"Content-Type": "text/xml"}

    return app
