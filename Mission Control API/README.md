# Mission Control API

Read-only FastAPI layer that exposes the three job agents in `Desktop/Email Agent`
(SCOUT = job finder, CONNECT = link resolver, APPLY = email agent) to a React frontend.
It only reads the agents' state files; the agents are not modified and never written to.

## Run

```
pip install -r requirements.txt
uvicorn mission_control.main:app --reload --port 8080   # 8000 = Agent 1's backend, 8001 is Windows-reserved here
```

Swagger UI: http://localhost:8080/docs. Paths and polling settings are in `config.yaml`
(override the file with `MC_CONFIG=<path>`).

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Per-agent `scanning` / `processing` / `idle` / `no_state`, derived from how recently each agent's section of its state file changed (`active_window_seconds`). |
| `GET /api/missions` | One row per job: title, company, platform, discovered_by/at, `stage` (`discovered → recruiter_found → application_ready → sent`), `progress` (25/50/75/100), recruiter email status, application status. |
| `GET /api/missions/{id}` | Everything above plus JD text (when SCOUT recorded one), recruiter info, draft letter/subject/CV files, and the `timeline` of stage transitions. |
| `GET /api/stats` | Summary-bar counts: jobs discovered today, recruiters verified, applications sent, awaiting approval, manual lookups needed, active automations. |
| `GET /api/events?limit=` | Recent events (same shape as the websocket). |
| `WS /ws/events` | Pushes `{type, mission_id, agent, timestamp, data}` as transitions happen. Types: `job_found`, `recruiter_verified`, `draft_ready`, `sent`, plus `manual_lookup_needed`, `rejected`, and `connected`/`ping` housekeeping frames. |

## How it works

A background task re-reads `state.json` (APPLY `pending` + CONNECT `links`) and
`scout_state.json` (SCOUT) every `poll_interval_seconds`, rebuilds the mission list and
diffs it against the previous build; only transitions become events. Reads retry on a
truncated/locked file and fall back to the last good snapshot, so an agent writing
mid-poll never crashes the service. Observed events are appended to this service's own
`events.jsonl` so mission timelines survive restarts.

## SCOUT state file

There is no SCOUT code on this machine yet, so the service reads an agreed shape from
`scout_state_file` and treats a missing file as "no SCOUT data":

```json
{"jobs": [{"url": "https://...", "title": "QA Lead", "company": "Acme", "platform": "indeed",
           "found_at": "2026-09-19T10:00:00", "description": "optional JD text"}]}
```

Jobs that CONNECT has processed are shown as discovered even when this file is absent.

## Tests

`pytest`
