# Job Application Email Agent

Watches a Gmail inbox for recruiter/job emails, scores them with Gemini, drafts a
cover letter (tailored to the JD when one exists, default template otherwise),
attaches the **currently active CV**, and waits for the owner's explicit
APPROVE reply before anything is sent.

## Setup

1. `pip install -r requirements.txt`
2. Gemini key: create one at https://aistudio.google.com/apikey, copy `.env.example` → `.env`, fill `GEMINI_API_KEY`.
3. Gmail API:
   - Google Cloud Console → new project → enable **Gmail API**.
   - OAuth consent screen (External, add `qanitabokhari5@gmail.com` as a test user).
   - Credentials → OAuth client ID → **Desktop app** → download JSON → save as `credentials.json` here.
   - First run opens a browser to log in as the Gmail account; the token is cached in `token.json`.
4. Drop the CV into `cv/` (PDF and/or DOCX). The newest file of each type is "active" and both are attached. Replace the file at any time — no code changes.

## Run

```
python -m agent.main profile      # check what the agent reads from the active CV
python -m agent.main once         # one pass: approvals first, then new inbox mail
python -m agent.main watch        # poll continuously (interval in config.yaml)
python -m agent.main score <id>   # dry-run scoring/JD extraction for one message id
```

## Flow

| Confidence | Action |
|---|---|
| HIGH | Extract JD → tailored letter (or default template if not confidently possible) → Gmail draft replying to the literal sender address with CV attached → `[JobAgent] Approval needed` email to owner |
| MEDIUM | `[JobAgent] Manual review` email to owner; no draft |
| LOW | Labelled `JobAgent/Low`, otherwise untouched |

Approval: reply to the approval email with `APPROVE` (first word) to send the draft, or `REJECT` to leave it in Drafts. No reply = nothing happens.

Every processed email gets the `JobAgent/Processed` label so it is never scored twice. `state.json` tracks drafts awaiting approval.

## Guarantees (spec §6)

- Reply address is always `From:` of the original email — never inferred.
- Cover letters use only JD content + active CV content (prompts in `prompts/`).
- CV files are read and attached, never modified.
- Nothing is sent without an APPROVE reply from the owner.
- Candidate name/role/skills come from the CV each run; nothing is hardcoded.

## Link Resolver Agent (Agent 3)

Bridges the Job Finder Agent (Agent 1) and this Email Agent (Agent 2). It reads Agent 1's
notification emails (subject containing `New SQA job:` by default — see
`link_resolver.notification` in `config.yaml`), opens every job link in them and:

| Platform | What happens |
|---|---|
| Indeed, Glassdoor, Remotive, WeWorkRemotely, Himalayas, anything else | Fetch the posting page (robots.txt + per-domain rate limit respected). If a recruiter/HR email is **literally on the page**, the job goes through the exact same JD → cover letter → Gmail draft → `[JobAgent] Approval needed` flow as above. Otherwise the owner gets a `[JobAgent] Manual review needed` email with the link, title and company. |
| LinkedIn | Only the single public posting page is read (title, company, poster name if shown). No email lookup of any kind. Owner gets a `[JobAgent] Manual lookup needed (LinkedIn)` email. LinkedIn's robots.txt disallows fetching job pages, so this usually contains the link only. |

Guarantees: an email is never inferred or guessed — Gemini can only choose among addresses a regex
already found on the page, and its answer is checked against that list again. No browser extensions
or third-party scraping/lookup tools are used. Nothing is sent without the owner's APPROVE reply.

```
python -m agent.link_resolver once             # process new "New SQA job:" notifications + approvals
python -m agent.link_resolver watch            # poll continuously
python -m agent.link_resolver resolve <url>    # dry run one link (no Gmail, no drafts, no state)
python -m agent.link_resolver links <msg_id>   # show links found in one notification
```

Processed notifications get the `JobAgent/LinkResolved` label; each URL's outcome is recorded in
`state.json` under `links` so the same job is never drafted twice. Optional: set
`link_resolver.fetch.playwright_fallback: true` (after `pip install playwright && playwright install chromium`)
to render JS-only pages in a headless browser — still the same single URL, nothing more.

## Notification channels

Every owner notification (approval needed, manual review, LinkedIn lookup, sent confirmation) goes
to all channels listed under `notifications.channels` in `config.yaml`: `email` (Gmail, always the
primary — the approval loop watches its thread), `slack` (incoming webhook) and `whatsapp`
(CallMeBot free API, a single HTTP GET). Secrets go in `.env` (see `.env.example`). Slack/WhatsApp
failures are logged and never block the email or the run.

```
python -m agent.notifications test   # sends a sample message to every enabled channel
```

CallMeBot setup: on WhatsApp, send `I allow callmebot to send me messages` to CallMeBot's number
(see https://www.callmebot.com/blog/free-api-whatsapp-messages/); it replies with your API key.
Put your phone (E.164) and that key in `.env` as `CALLMEBOT_PHONE` / `CALLMEBOT_APIKEY`.

## Tests

`pip install pytest && pytest`
