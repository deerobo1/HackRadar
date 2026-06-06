# 🚀 HackRadar — AI-Powered Hackathon Notifier

> Automatically discover hackathons, filter them with Claude AI, and receive personalised Telegram or WhatsApp notifications — fully automated via GitHub Actions.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Claude](https://img.shields.io/badge/AI-Claude%20Sonnet-orange?logo=anthropic)
![GitHub Actions](https://img.shields.io/badge/Automation-GitHub%20Actions-2088FF?logo=githubactions)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📐 Architecture

```
devpost.py ──┐
unstop.py  ──┤
mlh.py     ──┼──► filter.py (Claude Sonnet) ──► notifier/ ──► telegram.py
watchlist.py─┤         │                              │       └► whatsapp.py
social.py  ──┤         ▼                              ▼
linkedin.py ─┘      config.yaml              db.py (seen/snoozed/dismissed)
                                                      ▲
                                              snooze.py (every 15 min)
```

All scrapers emit a **common dict** `{ name, url, description, deadline, source }`.  
`filter.py` sends each to Claude and returns `{ match, reason, tags }`.  
The notifier dispatcher reads a single config flag to route to Telegram or WhatsApp.  
The SQLite DB is the single source of truth for all state.

---

## 📁 Folder Structure

```
hackradar/
├── scrapers/
│   ├── devpost.py          # Devpost hackathon listings
│   ├── unstop.py           # Unstop listings
│   ├── mlh.py              # MLH season schedule
│   ├── watchlist.py        # Hash-diff monitor for company blog/careers pages
│   ├── social.py           # Nitter RSS feed parser (Twitter/X hackathon mentions)
│   └── linkedin.py         # LinkedIn scraper (run LOCALLY — not in CI)
├── notifier/
│   ├── __init__.py         # Dispatcher (reads config to choose channel)
│   ├── telegram.py         # Rich messages + inline keyboard (Register/Snooze/Dismiss)
│   └── whatsapp.py         # Twilio WhatsApp + reply-keyword action handler
├── filter.py               # Claude Sonnet AI relevance filter
├── snooze.py               # Re-fires due snoozed items through dispatcher
├── db.py                   # SQLite setup — seen, snoozed, dismissed tables
├── main.py                 # Orchestrator: scrape → filter → notify
├── config.yaml             # All user settings (interests, tokens, URLs)
├── requirements.txt
└── .github/workflows/
    ├── run.yml             # Cron every 12 h — scrape + filter + notify
    └── snooze_check.yml    # Cron every 15 min — re-fire snoozed items
```

---

## ⚙️ Setup

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/hackradar.git
cd hackradar
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure `config.yaml`

Edit `config.yaml` with your details:

```yaml
# Your interest profile — plain English, no redeployment needed to change
interest_profile: |
  I am interested in AI/ML hackathons, developer tools, fintech, and open source.
  I prefer online or hybrid events. I like prizes above $5,000 USD.
  I am a backend Python developer with ML experience.

# Notification channel: "telegram" or "whatsapp"
notification_channel: telegram

# Snooze durations
snooze_duration_hours: 48

# Telegram
telegram:
  bot_token: YOUR_TELEGRAM_BOT_TOKEN
  chat_id: YOUR_CHAT_ID

# WhatsApp (Twilio)
whatsapp:
  account_sid: YOUR_TWILIO_ACCOUNT_SID
  auth_token: YOUR_TWILIO_AUTH_TOKEN
  from_number: "whatsapp:+14155238886"   # Twilio sandbox number
  to_number: "whatsapp:+YOUR_NUMBER"

# Watchlist — company blog/careers pages to monitor for hackathon posts
watchlist_urls:
  - https://devpost.com/hackathons
  - https://unstop.com/hackathons
  - https://fellowship.mlh.io/programs/open-source
  - https://developer.microsoft.com/en-us/reactor/
  - https://events.google.com/io/

# Nitter instances for Twitter/X RSS (social.py)
nitter_rss_feeds:
  - https://nitter.net/search/rss?q=%23hackathon
  - https://nitter.net/search/rss?q=%23builtwithAI
  - https://nitter.net/search/rss?q=hackathon+2025

# LinkedIn (run locally — not in GitHub Actions CI)
linkedin:
  email: YOUR_LINKEDIN_EMAIL
  password: YOUR_LINKEDIN_PASSWORD
  hashtags:
    - hackathon
    - builtwithAI
  company_pages:
    - microsoft
    - google
    - devpost
```

### 3. Set Environment Variables (local run)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export TWILIO_ACCOUNT_SID="..."
export TWILIO_AUTH_TOKEN="..."
export LINKEDIN_EMAIL="..."
export LINKEDIN_PASSWORD="..."
```

On Windows (PowerShell):
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

### 4. Run Locally

```bash
# Full pipeline: scrape → filter → notify
python main.py

# Re-fire snoozed items
python snooze.py

# Run LinkedIn scraper locally (not in CI)
python scrapers/linkedin.py
```

---

## 🤖 GitHub Actions Automation

### Workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `run.yml` | Every 12 hours | Full scrape → filter → notify pipeline |
| `snooze_check.yml` | Every 15 minutes | Re-fire due snoozed hackathons |

### Required GitHub Secrets

Go to **Settings → Secrets and Variables → Actions** and add:

| Secret Name | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram user/group chat ID |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID (WhatsApp only) |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token (WhatsApp only) |

> **Note**: `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` are intentionally **not** stored as GitHub Secrets because the LinkedIn scraper is designed to run locally (see below).

---

## 📱 Notification Actions

### Telegram
The bot sends an inline keyboard with three buttons:

| Button | Action |
|---|---|
| ✅ **Register** | Opens hackathon URL + sets a deadline reminder 2 days before close |
| ⏰ **Snooze** | Re-fires the notification after the configured snooze duration |
| ❌ **Dismiss** | Permanently skips this hackathon |

### WhatsApp
Since WhatsApp doesn't support inline buttons, actions are triggered by reply keywords:

| Reply | Action |
|---|---|
| `R` | Register — opens link + sets reminder |
| `S` | Snooze — re-fires after snooze duration |
| `D` | Dismiss — permanently skip |

Set up a Twilio webhook to point to your server/ngrok endpoint for incoming messages.

---

## ⚠️ LinkedIn Scraper — Run Locally Only

LinkedIn aggressively blocks GitHub Actions IP ranges. The `scrapers/linkedin.py` scraper:

- **Must be run locally** or via a residential proxy
- Uses Selenium with a headless Chrome browser
- Requires a dedicated throwaway LinkedIn account (do **not** use your main account)
- Will not be triggered by the CI workflows

To run manually:
```bash
# Make sure LINKEDIN_EMAIL and LINKEDIN_PASSWORD are set in your environment
python scrapers/linkedin.py
```

Consider running this on a schedule via your local machine's Task Scheduler (Windows) or cron (macOS/Linux).

---

## 🧠 AI Filter (Claude Sonnet)

`filter.py` sends each discovered hackathon to **Claude Sonnet** with:
- Your plain-English interest profile from `config.yaml`
- The hackathon's name, description, deadline, and source URL

Claude returns structured JSON:
```json
{
  "match": true,
  "reason": "AI/ML focus with $10k prize pool, online format matches your profile",
  "tags": ["AI", "ML", "online", "prize > $5k"]
}
```

Only hackathons where `match: true` are forwarded to the notifier.

---

## 🗄️ Database Schema

SQLite database stored at `hackradar.db`:

```sql
-- Tracks all hackathons we've seen (prevents duplicate notifications)
CREATE TABLE seen (
    url TEXT PRIMARY KEY,
    name TEXT,
    source TEXT,
    first_seen_at TEXT
);

-- Hackathons to re-notify after a delay
CREATE TABLE snoozed (
    url TEXT PRIMARY KEY,
    name TEXT,
    data TEXT,          -- full hackathon JSON blob
    fire_at TEXT        -- ISO8601 datetime to re-notify
);

-- Permanently skipped hackathons
CREATE TABLE dismissed (
    url TEXT PRIMARY KEY,
    name TEXT,
    dismissed_at TEXT
);
```

---

## 🔧 Customising Your Interest Profile

Edit the `interest_profile` field in `config.yaml` — no code changes or redeployment needed:

```yaml
interest_profile: |
  I am interested in web3, NFT tooling, and DeFi hackathons.
  I prefer in-person events in Europe.
  I am a Solidity and TypeScript developer.
```

---

## 📦 Dependencies

```
beautifulsoup4      # HTML scraping
feedparser          # RSS/Atom feed parsing
requests            # HTTP client
pyyaml              # YAML config loading
anthropic           # Claude Sonnet API
python-telegram-bot # Telegram bot + inline keyboards
twilio              # WhatsApp via Twilio
selenium            # LinkedIn headless browser scraping
webdriver-manager   # Auto-manages ChromeDriver
hashlib             # Page hash diffing (watchlist monitor)
```

---

## 🤝 Contributing

Pull requests welcome! Please open an issue first for major changes.

---

## 📄 License

MIT © 2025 HackRadar Contributors
