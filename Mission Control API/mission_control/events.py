"""Poll the state files, diff against the last snapshot, emit events.

No message queue: an asyncio task re-reads the files every `poll_interval_seconds`,
rebuilds the missions, and compares with the previous build. Only transitions produce
events. Observed events are appended to this service's own `events.jsonl` so mission
timelines survive restarts (the agents' files are never written).
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .missions import MissionDetail, build_missions
from .state_reader import SafeJsonFile

log = logging.getLogger(__name__)

AGENTS = {"SCOUT": "job finder", "CONNECT": "link resolver", "APPLY": "email agent"}
ACTIVE_LABEL = {"SCOUT": "scanning", "CONNECT": "processing", "APPLY": "processing"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def diff_missions(old: dict[str, MissionDetail], new: dict[str, MissionDetail], ts: str) -> list[dict]:
    """Events for every stage transition between two snapshots."""
    events: list[dict] = []

    def ev(type_: str, m: MissionDetail, agent: str, **data):
        events.append({"type": type_, "mission_id": m.id, "agent": agent, "timestamp": ts,
                       "data": {"title": m.title, "company": m.company, "platform": m.platform, "job_url": m.job_url, **data}})

    for mid, m in new.items():
        prev = old.get(mid)
        if prev is None:
            ev("job_found", m, m.discovered_by)
        if m.recruiter_email_status == "verified" and (prev is None or prev.recruiter_email_status != "verified"):
            ev("recruiter_verified", m, "CONNECT" if m.job_url else "APPLY", recruiter_email=m.recruiter_email)
        if m.recruiter_email_status in ("manual_review", "manual_lookup_needed") and \
                (prev is None or prev.recruiter_email_status != m.recruiter_email_status):
            ev("manual_lookup_needed", m, "CONNECT", status=m.recruiter_email_status, note=m.notes)
        if m.application_status == "awaiting_approval" and (prev is None or prev.application_status != "awaiting_approval"):
            ev("draft_ready", m, "APPLY", draft_id=m.draft_id, draft_url=m.draft_url)
        if m.application_status == "sent" and (prev is None or prev.application_status != "sent"):
            ev("sent", m, "APPLY", recruiter_email=m.recruiter_email)
        if m.application_status == "rejected" and prev is not None and prev.application_status != "rejected":
            ev("rejected", m, "APPLY", draft_id=m.draft_id)
    return events


class Poller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.apply_file = SafeJsonFile(cfg["apply_state_file"], default={"pending": {}, "links": {}})
        self.scout_file = SafeJsonFile(cfg["scout_state_file"], default={"jobs": []})
        self.events_log: Path = cfg["events_log"]
        self.interval = float(cfg["poll_interval_seconds"])
        self.active_window = float(cfg["active_window_seconds"])
        self.missions: dict[str, MissionDetail] = {}
        self.events: list[dict] = self._load_log()
        self.last_change: dict[str, float] = {"SCOUT": 0.0, "CONNECT": 0.0, "APPLY": 0.0}
        self._sections: dict[str, Any] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self.refresh(initial=True)

    # ---- polling -------------------------------------------------------
    def refresh(self, initial: bool = False) -> list[dict]:
        """Re-read the files, update missions and per-agent activity, return new events."""
        apply_state = self.apply_file.read() or {}
        scout_state = self.scout_file.read()
        sections = {"SCOUT": scout_state, "CONNECT": apply_state.get("links"), "APPLY": apply_state.get("pending")}
        now = time.time()
        for agent, data in sections.items():
            if data != self._sections.get(agent):
                # On the first read use the file's own mtime so a fresh start doesn't look "active".
                mtime = self.scout_file.mtime() if agent == "SCOUT" else self.apply_file.mtime()
                self.last_change[agent] = mtime if initial else now
        self._sections = sections

        new = build_missions(scout_state, apply_state, self.apply_file.mtime())
        ts = _now_iso()
        events = diff_missions(self.missions, new, ts)
        self.missions = new
        if initial:
            # Seed timelines for missions the log has never seen, stamped with the file mtime.
            seen = {e["mission_id"] for e in self.events}
            seeded = [e for e in events if e["mission_id"] not in seen]
            for e in seeded:
                e["timestamp"] = new[e["mission_id"]].discovered_at or ts
                e["seeded"] = True
            self._append_log(seeded)
            return []
        self._append_log(events)
        return events

    async def run(self) -> None:
        while True:
            try:
                events = await asyncio.to_thread(self.refresh)
                for e in events:
                    log.info("event %s %s (%s)", e["type"], e["mission_id"], e["agent"])
                self.push_events(events)
            except Exception:  # noqa: BLE001 - keep polling no matter what
                log.exception("poll failed")
            await asyncio.sleep(self.interval)

    def push_events(self, events: list[dict]) -> None:
        """Deliver already-computed events to current websocket subscribers. Used by this
        loop's own tick, and by request handlers that just made an out-of-band state change
        (e.g. a manual approve/reject) so subscribers don't have to wait for the next tick."""
        for e in events:
            for q in list(self._subscribers):
                q.put_nowait(e)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---- subscribers ---------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- derived views -------------------------------------------------
    def agent_status(self) -> dict[str, dict]:
        now = time.time()
        out = {}
        for agent, desc in AGENTS.items():
            last = self.last_change[agent]
            active = last and now - last <= self.active_window
            present = self.scout_file.exists if agent == "SCOUT" else self.apply_file.exists
            out[agent] = {
                "agent": agent, "description": desc,
                "status": ACTIVE_LABEL[agent] if active else ("idle" if present else "no_state"),
                "last_activity": datetime.fromtimestamp(last).isoformat(timespec="seconds") if last else None,
                "state_file": str(self.scout_file.path if agent == "SCOUT" else self.apply_file.path),
            }
        return out

    def timeline(self, mission_id: str) -> list[dict]:
        return [e for e in self.events if e["mission_id"] == mission_id]

    # ---- event log -----------------------------------------------------
    def _load_log(self) -> list[dict]:
        if not self.events_log.exists():
            return []
        out = []
        for line in self.events_log.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _append_log(self, events: list[dict]) -> None:
        if not events:
            return
        self.events.extend(events)
        with self.events_log.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
