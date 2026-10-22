# SQA Job Agent — Architecture Blueprint & Setup Guide

Real-time remote QA/SQA job monitoring: backend polls multiple job sources on
a schedule, normalizes and deduplicates them, classifies and scores them, stores
them, and pushes an instant notification (Telegram/Discord/Email/WhatsApp)
when a new high-scoring one appears. A React dashboard shows the live feed and
lets you edit filters without touching code.

## Why LinkedIn / Indeed / Glassdoor aren't scraped directly

These platforms publish no public job-search API and their Terms of Service
prohibit automated scraping/bots (and they actively detect and block it).
Building a scraper against them isn't a "harder version" of the same task —
it's a different, unsupported thing. Instead, `backend/app/adapters/jsearch.py`
wires in **JSearch** (via RapidAPI), a licensed service that legally
re-indexes those platforms' listings. Get a free key at
rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch, set `JSEARCH_ENABLED=true`
and `RAPIDAPI_KEY=...` in `backend/.env`, and that coverage turns on with no
code changes.

## Architecture

```
┌─────────────────┐     poll every N min      ┌──────────────────────┐
│  APScheduler     │ ─────────────────────────▶│  Adapters (13)        │
│  background job  │                            │  RemoteOK, Remotive,  │
└─────────────────┘                            │  Himalayas, Arbeitnow,│
                                                 │  Jobicy, WWR, Jobspresso,
                                                 │  Pangian, Authentic   │
                                                 │  Jobs, remote.io,     │
                                                 │  Dribbble, Working    │
                                                 │  Nomads, JSearch      │
                                                 └──────────┬───────────┘
                                                            │ RawJob[]
                                                            ▼
                                                 ┌─────────────────────────┐
                                                 │ Normalize + fuzzy dedup  │
                                                 │ Local HF classifier      │
                                                 │ + weighted scorer        │
                                                 └──────────┬──────────────┘
                                                            │ score/filter
                                                            ▼
                                    ┌──────────────┐  save ┌──────────────────┐
                                    │  SQLite/      │◀─────│  Normalized jobs │
                                    │  Postgres DB  │      │  + score details │
                                    └──────┬────────┘      └──────────────────┘
                                           │ high-scoring new job
                                           ▼
                                 ┌────────────────────┐
                                 │  Notifiers          │
                                 │  Telegram / Discord │
                                 │  Email / WhatsApp   │
                                 └────────────────────┘

┌─────────────────┐   REST API (FastAPI)   ┌─────────────────┐
│  React dashboard │◀──────────────────────▶│  /api/jobs        │
│  (Vite+Tailwind) │   GET /api/jobs         │  /api/preferences │
│                   │   PUT /api/preferences │  /api/sources     │
│                   │   POST /api/poll-now   │  /api/poll-now    │
└─────────────────┘                        └─────────────────┘
```

## File structure

```
sqa-job-agent/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app + REST endpoints
│   │   ├── config.py          # env-driven settings (all placeholders)
│   │   ├── database.py        # SQLAlchemy engine/session
│   │   ├── models.py          # Job, Preference tables
│   │   ├── schemas.py         # Pydantic request/response models
│   │   ├── filters.py         # keyword/location matching
│   │   ├── scheduler.py       # APScheduler polling loop
│   │   ├── adapters/          # one file per job source (pluggable)
│   │   │   ├── base.py
│   │   │   ├── remoteok.py / remotive.py / himalayas.py / arbeitnow.py
│   │   │   ├── jobicy.py / working_nomads.py
│   │   │   ├── rss_sources.py # WWR, Jobspresso, Pangian, Authentic Jobs, remote.io, Dribbble
│   │   │   ├── jsearch.py     # licensed LinkedIn/Indeed/Glassdoor coverage (opt-in)
│   │   │   └── __init__.py    # ADAPTERS registry — add new sources here only
│   │   ├── normalizer.py      # RawJob -> NormalizedJob (pure function)
│   │   ├── dedup.py           # exact + rapidfuzz cross-board duplicate check
│   │   ├── classifier.py      # local Hugging Face models (MiniLM + BART-MNLI) + rules
│   │   ├── scoring.py         # weighted 0-100 score (env-configurable weights)
│   │   └── notifiers/
│   │       ├── base.py / telegram.py / discord.py / email.py
│   │       ├── whatsapp.py     # Twilio WhatsApp API; no-op unless configured
│   │       └── __init__.py    # dispatches to every channel in PREFERRED_NOTIFIER
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── App.jsx             # dashboard shell
    │   ├── api.js               # fetch wrapper for the backend REST API
    │   └── components/
    │       ├── JobFeed.jsx      # live job list
    │       ├── Filters.jsx      # keyword/location/source filter bar
    │       └── NotificationSettings.jsx  # edit alert keywords/locations
    ├── package.json
    └── .env.example
```

## Setup — Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `DATABASE_URL` — Postgres/Supabase is the recommended default; switch to `sqlite:///./jobs.db` for zero-setup local dev
- `PREFERRED_NOTIFIER` — `telegram`, `discord`, `email`, or any comma-separated combination
- Fill in credentials only for the channel(s) you chose (see "Notification setup" below)

Run it (bind to loopback only — the API has no user accounts; the `ADMIN_API_KEY`
header guards mutating routes, but read endpoints are open by design):
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Set `ADMIN_API_KEY` in `backend/.env` (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
and the same value as `VITE_ADMIN_API_KEY` in `frontend/.env`. Every POST/PUT/PATCH/DELETE
(`/api/admin/*`, `/api/poll-now`, `/api/resume`, `/api/blocklist`, job status/notes/snooze,
preferences) requires the `X-Api-Key` header and returns 401 without it. Never expose the
port beyond localhost without a reverse proxy that adds authentication.

Tests: `cd backend && python -m pytest tests -q`
Visit `http://localhost:8000/docs` for interactive API docs (auto-generated by FastAPI).
On startup, it polls every configured source once immediately, then every
`POLL_INTERVAL_MINUTES` after that.

## Setup — Frontend

```bash
cd frontend
npm install
cp .env.example .env   # point VITE_API_BASE_URL at your backend if not localhost:8000
npm run dev
```
Visit `http://localhost:5173`. The dashboard polls `/api/jobs` every 60s and
has a "Check now" button that triggers an immediate `/api/poll-now` call.

## Notification setup

**Telegram**
1. Message `@BotFather` on Telegram → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Message your new bot once (so it can message you back), then get your chat ID via
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id` into `TELEGRAM_CHAT_ID`.

**Discord**
1. Server Settings → Integrations → Webhooks → New Webhook → copy URL into `DISCORD_WEBHOOK_URL`.

**Email**
1. Gmail: Google Account → Security → 2-Step Verification → App Passwords → generate one.
2. Put your address in `SMTP_USER`, the 16-character app password in `SMTP_PASS`, and where alerts go in `EMAIL_TO`.

## Deployment for 24/7 uptime

The backend needs to run continuously for the scheduler to keep polling.
Options, cheapest first:
- **A small always-on VM/container** (Fly.io, Railway, Render free/hobby tier) running `uvicorn app.main:app`.
- **Docker**: wrap `backend/` in a container (`CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`) and deploy anywhere that runs containers.
- Serve `frontend/` as a static build (`npm run build` → `dist/`) from any static host (Vercel, Netlify, Cloudflare Pages), pointed at your backend's public URL via `VITE_API_BASE_URL`.

## Adding a new job source later

1. Add a file in `backend/app/adapters/` with a class extending `BaseAdapter`
   (see `base.py`) that returns a list of `RawJob`.
2. Register an instance of it in `backend/app/adapters/__init__.py`'s `ADAPTERS` list.
3. Done — the scheduler, filter, dedup, storage, and notification logic all
   pick it up automatically; no other file changes.

## Rate limits & attribution (per source)

Several sources require an attribution backlink and/or cap request frequency
— these are respected by the default `POLL_INTERVAL_MINUTES=15`:
- **RemoteOK / Remotive / Himalayas / Jobicy / We Work Remotely** — all require crediting the source with a backlink if you display their data publicly (a private dashboard for personal use is fine either way). Remotive specifically caps at ~4 requests/day; Jobicy at ~1/hour.
- **JSearch** — subject to your RapidAPI plan's rate limit (free tier is metered).

If you deploy this for a team (not just yourself), read each source's
`/api` or terms page before scaling up polling frequency or displaying their
data outside your private dashboard.

## Pipeline modules and runtime controls

Each poll runs every adapter result through
`normalize → keyword/location filter → dedup → classify → score → save → notify`:

- **`normalizer.py`** — pure function mapping any adapter's `RawJob` to a
  `NormalizedJob` (title, company, location, salary_min/max/currency,
  description, posted_at, apply_url, source).
- **`dedup.py`** — besides the `(source, external_id)` unique constraint, a
  fuzzy check with `rapidfuzz` compares the punctuation-stripped title against
  company name + apply-URL domain of jobs discovered in the last
  `DEDUP_LOOKBACK_DAYS` (default 30) to catch cross-posted roles. Thresholds:
  `DEDUP_TITLE_THRESHOLD` (90) / `DEDUP_COMPANY_THRESHOLD` (85).
- **`classifier.py`** — free, local Hugging Face inference (no paid LLM API):
  - `HF_RELEVANCE_MODEL` (default `sentence-transformers/all-MiniLM-L6-v2`)
    embeds title+description and compares cosine similarity against SQA
    reference phrases → `is_sqa_relevant` + `relevance_score`
    (`HF_RELEVANCE_THRESHOLD`, default 0.40).
  - `HF_ZEROSHOT_MODEL` (default `facebook/bart-large-mnli`) zero-shot
    classifies `seniority` (junior/mid/senior/lead/unknown), `is_remote`,
    `is_worldwide`. Unambiguous titles/locations short-circuit the model.
  - `pakistan_eligible` is rule-based (regex for "US only", "must be authorized
    to work in …", restricted locations, etc.).
  - `required_skills` is keyword extraction against a maintained tooling list
    (Selenium, Cypress, Playwright, Postman, JIRA, API testing, …).
  - Models load once at startup (`HF_PRELOAD=true`) and are cached; `HF_DEVICE`
    selects `cpu` (default) or `cuda`. First run downloads ~1.7 GB. If the
    libraries/models are unavailable the classifier falls back to heuristics
    and `GET /api/health` reports `"classifier_backend": "rules"`.
- **`scoring.py`** — weighted 0-100 score: SQA relevance 40 (× relevance_score),
  remote 20, junior-friendly 15, Pakistan-eligible 15, posted <24h 10. Weights
  are `SCORE_*` env vars and are normalised to 100.
- **Notifications** fire for every saved job with `score >= NOTIFY_SCORE_THRESHOLD`
  (default 0 = every job that passes the keyword filter; raise it to cut
  noise). The dashboard's "Notification score threshold" preference overrides
  it when non-zero. WhatsApp (Twilio) is a no-op unless `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` and `WHATSAPP_TO` are all set;
  add `whatsapp` to `PREFERRED_NOTIFIER` to enable it.

`GET /api/jobs` supports `keyword`, `location`, `source`, `seniority`,
`min_score`, `sort=score|newest` (score is the default), and `limit`. The
dashboard shows the score badge, seniority/remote/eligibility chips and skill
tags per job, with a seniority dropdown and minimum-score slider.

**Not scraped, by design:** LinkedIn, Indeed, Glassdoor and Wellfound. None
offers a public job API and all prohibit automated access in their Terms of
Service — switching tools (Playwright, requests, …) doesn't change that.
LinkedIn/Indeed/Glassdoor listings are available only through the licensed
JSearch adapter (`JSEARCH_ENABLED=true`); Wellfound is skipped until an
official API or licensed aggregator exists (see `adapters/__init__.py`).
