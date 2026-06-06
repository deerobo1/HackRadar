"""
filter.py — HackRadar LLM relevance filter
===========================================
Sends each hackathon to Claude Sonnet with the user's interest profile.
Returns only hackathons that Claude marks as a match.

Expected Claude response (JSON):
    {
        "match": true,
        "reason": "AI/ML focus with $10k prize pool...",
        "tags": ["AI", "ML", "online", "prize > $5k"]
    }
"""

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are HackRadar, an AI assistant that filters hackathon listings \
for a developer based on their interest profile.

Given a hackathon's details and the user's interest profile, you decide whether \
the hackathon is a good match.

You MUST respond with ONLY valid JSON — no markdown, no extra text — in this exact schema:
{
  "match": <true|false>,
  "reason": "<one sentence explaining the decision>",
  "tags": ["<tag1>", "<tag2>", ...]
}

Tags should be concise labels extracted from the hackathon (e.g. "AI", "ML", "fintech", \
"online", "in-person", "prize > $5k", "beginner-friendly", etc.)."""


def _build_user_message(hackathon: dict, interest_profile: str) -> str:
    return f"""User interest profile:
{interest_profile}

Hackathon to evaluate:
- Name: {hackathon.get('name', 'N/A')}
- URL: {hackathon.get('url', 'N/A')}
- Source: {hackathon.get('source', 'N/A')}
- Deadline: {hackathon.get('deadline', 'N/A')}
- Description:
{hackathon.get('description', 'No description provided.')}

Does this hackathon match the user's interest profile? Respond with JSON only."""


def _parse_filter_response(response_text: str, hackathon_name: str) -> dict[str, Any]:
    """
    Parse and validate Claude's JSON response.
    Returns a dict with keys: match (bool), reason (str), tags (list).
    Falls back to match=False on any parse error.
    """
    try:
        # Strip any accidental markdown fences
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        data = json.loads(text)

        # Validate required fields
        if not isinstance(data.get("match"), bool):
            raise ValueError(f"'match' field must be boolean, got: {data.get('match')!r}")

        return {
            "match": bool(data["match"]),
            "reason": str(data.get("reason", "")),
            "tags": list(data.get("tags", [])),
        }

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning(
            "Failed to parse Claude response for '%s': %s\nRaw: %s",
            hackathon_name, exc, response_text[:300]
        )
        return {"match": False, "reason": f"Parse error: {exc}", "tags": []}


def filter_hackathons(hackathons: list[dict], cfg) -> list[dict]:
    """
    Filter a list of hackathon dicts through Claude Sonnet.

    Returns a new list containing only hackathons that matched, with two
    extra keys added:
        - filter_reason  (str)  : Claude's reasoning
        - filter_tags    (list) : extracted tags

    Args:
        hackathons: list of hackathon dicts (name, url, description, deadline, source)
        cfg: loaded Config object

    Returns:
        list of matched hackathon dicts
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error(
            "ANTHROPIC_API_KEY is not set. Skipping LLM filter — "
            "all hackathons will be treated as non-matches."
        )
        return []

    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. Run: pip install anthropic")
        return []

    client = anthropic.Anthropic(api_key=api_key)
    matched: list[dict] = []

    for hackathon in hackathons:
        name = hackathon.get("name", "unknown")
        log.info("Filtering: %s", name)

        try:
            message = client.messages.create(
                model=cfg.anthropic.model,
                max_tokens=cfg.anthropic.max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": _build_user_message(hackathon, cfg.interest_profile),
                    }
                ],
            )

            response_text = message.content[0].text if message.content else ""
            result = _parse_filter_response(response_text, name)

            log.info(
                "[%s] match=%s tags=%s — %s",
                name,
                result["match"],
                result["tags"],
                result["reason"][:80],
            )

            if result["match"]:
                enriched = dict(hackathon)
                enriched["filter_reason"] = result["reason"]
                enriched["filter_tags"] = result["tags"]
                matched.append(enriched)

        except Exception as exc:
            log.error("Claude API error for '%s': %s", name, exc, exc_info=True)
            # On API error, skip rather than crash the whole run

    log.info(
        "Filter complete: %d/%d hackathons matched.", len(matched), len(hackathons)
    )
    return matched
