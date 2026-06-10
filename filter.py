"""
filter.py — HackRadar LLM relevance filter
===========================================
Sends each hackathon to Google Gemini Flash (free tier) with the user's
interest profile and returns only those that match.

Provider:  Google Gemini (free at aistudio.google.com)
Model:     gemini-2.0-flash  (default — fast, free, accurate)
API key:   Set GEMINI_API_KEY environment variable

Expected Gemini response (JSON):
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

# Auto-load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are HackRadar, an AI assistant that filters hackathon listings \
for a developer based on their interest profile.

Given a hackathon's details and the user's interest profile, you decide whether \
the hackathon is a good match.

You MUST respond with ONLY valid JSON — no markdown fences, no extra text — \
in this exact schema:
{
  "match": <true|false>,
  "reason": "<one sentence explaining the decision>",
  "tags": ["<tag1>", "<tag2>", ...]
}

Tags should be concise labels extracted from the hackathon \
(e.g. "AI", "ML", "fintech", "online", "in-person", "prize > $5k", "beginner-friendly")."""


def _build_prompt(hackathon: dict, interest_profile: str) -> str:
    """Build the full prompt to send to Gemini."""
    return f"""{_SYSTEM_PROMPT}

---

User interest profile:
{interest_profile}

Hackathon to evaluate:
- Name: {hackathon.get('name', 'N/A')}
- URL: {hackathon.get('url', 'N/A')}
- Source: {hackathon.get('source', 'N/A')}
- Deadline: {hackathon.get('deadline', 'N/A')}
- Prize: {hackathon.get('prize', 'N/A')}
- Description:
{hackathon.get('description', 'No description provided.')}

Does this hackathon match the user's interest profile? Respond with JSON only."""


# ---------------------------------------------------------------------------
# Response parser (shared — same JSON schema regardless of provider)
# ---------------------------------------------------------------------------

def _parse_response(response_text: str, hackathon_name: str) -> dict[str, Any]:
    """
    Parse and validate the LLM's JSON response.
    Falls back to match=False on any parse error rather than crashing.
    """
    try:
        text = response_text.strip()

        # Strip accidental markdown fences (```json ... ```)
        if text.startswith("```"):
            parts = text.split("```")
            # parts[1] is the content between first pair of fences
            text = parts[1].lstrip("json").strip() if len(parts) > 1 else text

        data = json.loads(text)

        if not isinstance(data.get("match"), bool):
            raise ValueError(
                f"'match' field must be boolean, got: {data.get('match')!r}"
            )

        return {
            "match": bool(data["match"]),
            "reason": str(data.get("reason", "")),
            "tags": list(data.get("tags", [])),
        }

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning(
            "Failed to parse LLM response for '%s': %s\nRaw: %s",
            hackathon_name, exc, response_text[:300],
        )
        return {"match": False, "reason": f"Parse error: {exc}", "tags": []}


# ---------------------------------------------------------------------------
# Gemini backend
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, cfg) -> str:
    """
    Send a prompt to Gemini Flash and return the raw text response.
    Uses the official google-genai SDK (google.genai package).
    Install: pip install google-genai
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        raise RuntimeError(
            "google-genai not installed. Run: pip install google-genai"
        )

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Get a free key at: https://aistudio.google.com/app/apikey"
        )

    client = genai.Client(api_key=api_key)
    model_name = cfg.gemini.model if hasattr(cfg, "gemini") else "gemini-2.0-flash"

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=512,
            response_mime_type="application/json",   # Forces valid JSON output
        ),
    )
    return response.text or ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_hackathons(hackathons: list[dict], cfg) -> list[dict]:
    """
    Filter a list of hackathon dicts through Gemini Flash.

    Returns only hackathons where Gemini returns match=true, with two
    extra keys added to each:
        - filter_reason  (str)  : model's one-sentence reasoning
        - filter_tags    (list) : extracted labels

    Args:
        hackathons : list of hackathon dicts (name, url, description, deadline, source)
        cfg        : loaded Config object

    Returns:
        list of matched hackathon dicts
    """
    # Quick guard — if no API key at all, skip gracefully
    if not os.environ.get("GEMINI_API_KEY", ""):
        log.error(
            "GEMINI_API_KEY is not set — skipping LLM filter. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
        return []

    matched: list[dict] = []

    for hackathon in hackathons:
        name = hackathon.get("name", "unknown")
        log.info("[filter] Evaluating: %s", name)

        try:
            prompt = _build_prompt(hackathon, cfg.interest_profile)
            raw_response = _call_gemini(prompt, cfg)
            result = _parse_response(raw_response, name)

            log.info(
                "[filter] %s | match=%s | tags=%s | %s",
                name[:50],
                result["match"],
                result["tags"],
                result["reason"][:80],
            )

            if result["match"]:
                enriched = dict(hackathon)
                enriched["filter_reason"] = result["reason"]
                enriched["filter_tags"] = result["tags"]
                matched.append(enriched)

        except RuntimeError as exc:
            # Config / import errors — stop immediately, don't waste API quota
            log.error("[filter] Fatal error: %s", exc)
            break

        except Exception as exc:
            # Per-hackathon API errors — log and continue
            log.error("[filter] Gemini API error for '%s': %s", name, exc, exc_info=True)

    log.info(
        "[filter] Done: %d/%d hackathons matched.", len(matched), len(hackathons)
    )
    return matched


# ---------------------------------------------------------------------------
# CLI quick-test  (python filter.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Minimal mock config
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        interest_profile=(
            "I am a backend Python developer interested in AI, ML, open-source, "
            "and developer tools hackathons. Online or hybrid preferred. "
            "Prize > $5k is a plus."
        ),
        gemini=SimpleNamespace(model="gemini-2.5-flash"),
    )

    # Sample hackathons to test
    test_hackathons = [
        {
            "name": "Google Cloud AI Hackathon",
            "url": "https://example.devpost.com",
            "description": "Build AI agents on Google Cloud. $50,000 in prizes. Online.",
            "deadline": "Jul 01, 2026",
            "source": "devpost",
            "prize": "$50,000",
        },
        {
            "name": "National Gaming Jam 2026",
            "url": "https://gaming-jam.io",
            "description": "48-hour game development competition. In-person only. Unity/Unreal.",
            "deadline": "Jul 15, 2026",
            "source": "unstop",
            "prize": "$1,000",
        },
    ]

    print("\n--- Running filter test with Gemini Flash ---\n")
    results = filter_hackathons(test_hackathons, cfg)
    print(f"\n--- Results: {len(results)}/{len(test_hackathons)} matched ---")
    for h in results:
        print(f"  MATCH: {h['name']}")
        print(f"  Reason: {h['filter_reason']}")
        print(f"  Tags: {h['filter_tags']}")
