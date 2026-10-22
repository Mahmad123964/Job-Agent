# Mission Control UI

A plain kanban board for the three job agents (SCOUT / CONNECT / APPLY). React + TypeScript +
Tailwind only — no 3D, no animation libraries, no command palette. Reads the Mission Control API
and stays in sync over its WebSocket; it never talks to the agents directly.

## Run

```
npm install
npm run dev        # http://localhost:3000
npm run build      # type-check + production bundle in dist/
```

The backend URL comes from `VITE_API_URL` (see `.env.example`; `.env` ships with
`http://localhost:8080`). The WebSocket URL is derived from it (`ws://localhost:8080/ws/events`).

> **Why 8080?** Port 8000 on this machine is occupied by Agent 1 (SCOUT)'s own backend. Port
> 8001 (the previous default) turned out to fall inside a Windows-reserved excluded port range,
> so the Mission Control API now runs on 8080: `uvicorn mission_control.main:app --port 8080`.
> To point at a different host/port: `VITE_API_URL=http://host:port npm run dev`, or edit `.env`.

## Board

One column per pipeline stage, computed client-side from the mission fields the API already
returns (`recruiter_email_status`, `application_status`) — no backend changes needed:

| Column | A mission is here when |
|---|---|
| New | Just discovered; recruiter email not yet resolved |
| Recruiter Found | `recruiter_email_status = verified` |
| Manual Review | `recruiter_email_status` is `manual_review` or `manual_lookup_needed` |
| Draft Created | `application_status = awaiting_approval` / `rejected` / `missing` |
| Sent | `application_status = sent` |
| Archived | Archived from the detail panel — **local only** (stored in the browser's `localStorage`), never sent to the backend |

Each card shows title, company, platform and a relative timestamp; an accent-colored badge marks
anything needing attention (manual review, rejected, missing draft). Click a card to open the
detail panel: JD, recruiter email, draft content, signals and the event timeline
(`GET /api/missions/{id}`), plus "Open posting" and "Open draft" links. The draft link carries
`?authuser=<owner email>` (read from `/api/status`) so it opens in the right Google account.

## Data flow

- On load: `/api/status`, `/api/missions`, `/api/stats`.
- `WS /ws/events` with exponential-backoff reconnect; any real event triggers a REST refresh
  (kept deliberately simple — no client-side event log or per-event animation). A 10s poll runs
  as a safety net if the socket is down.
- Backend unreachable → red banner, live indicator turns red/offline, the board keeps showing the
  last data it had.

## What changed from the previous ("Agent Orbit") version

The 3D core, orbit views, command palette, black box log and scheduler were removed along with
their dependencies (`three`, `@react-three/fiber`, `@react-three/drei`, `framer-motion`) — this
is a plain data-fetching + Tailwind app now. `api/client.ts`, `api/types.ts` and the `gmailLink`
helper in `lib/format.ts` are unchanged; the backend (Mission Control API) was not touched.
