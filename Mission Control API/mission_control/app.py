"""FastAPI app factory for the Mission Control API (see main.py for the uvicorn entry point)."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import agent2_bridge
from .config import load_config
from .events import Poller
from .missions import Mission, MissionDetail

log = logging.getLogger("mission_control")


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    poller = Poller(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poller.start()
        yield
        await poller.stop()

    app = FastAPI(title="Mission Control API", version="1.0", lifespan=lifespan)
    app.state.poller = poller
    app.add_middleware(CORSMiddleware, allow_origins=cfg["cors_origins"], allow_origin_regex=cfg.get("cors_origin_regex"),
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/status")
    def status() -> dict:
        agents = poller.agent_status()
        return {"agents": agents, "polled_at": datetime.now().isoformat(timespec="seconds"),
                "active_window_seconds": cfg["active_window_seconds"], "owner_email": cfg.get("owner_email")}

    @app.get("/api/missions", response_model=list[Mission])
    def missions() -> list[Mission]:
        items = [Mission(**m.model_dump(include=set(Mission.model_fields))) for m in poller.missions.values()]
        return sorted(items, key=lambda m: m.discovered_at or "", reverse=True)

    @app.get("/api/missions/{mission_id}", response_model=MissionDetail)
    def mission(mission_id: str) -> MissionDetail:
        m = poller.missions.get(mission_id)
        if not m:
            raise HTTPException(404, f"mission {mission_id} not found")
        return m.model_copy(update={"timeline": poller.timeline(mission_id)})

    @app.get("/api/stats")
    def stats() -> dict:
        today = date.today().isoformat()
        ms = list(poller.missions.values())
        agents = poller.agent_status()
        return {
            "jobs_discovered_today": sum(1 for m in ms if (m.discovered_at or "").startswith(today)),
            "jobs_discovered_total": len(ms),
            "recruiters_verified": sum(1 for m in ms if m.recruiter_email_status == "verified"),
            "awaiting_approval": sum(1 for m in ms if m.application_status == "awaiting_approval"),
            "applications_sent": sum(1 for m in ms if m.application_status == "sent"),
            "manual_lookups_needed": sum(1 for m in ms if m.recruiter_email_status in ("manual_review", "manual_lookup_needed")),
            "active_automations": sum(1 for a in agents.values() if a["status"] not in ("idle", "no_state")),
        }

    async def _act_on_mission(mission_id: str, action: str) -> dict:
        """Shared by /approve and /reject: validate the mission, run the action through Agent
        2's own approve_draft/reject_draft (see agent2_bridge.py), then refresh so both the
        HTTP response and any connected websocket clients reflect it immediately."""
        m = poller.missions.get(mission_id)
        if not m:
            raise HTTPException(404, f"mission {mission_id} not found")
        if not m.draft_id:
            raise HTTPException(409, "mission has no draft to act on")
        if m.application_status != "awaiting_approval":
            raise HTTPException(409, f"mission is '{m.application_status}', not awaiting approval")

        agents_dir, apply_state_file = cfg["agents_dir"], cfg["apply_state_file"]
        gmail_result = None
        try:
            approval_error = agent2_bridge.approval_error_type(agents_dir)
        except agent2_bridge.Agent2Unavailable as e:
            raise HTTPException(502, str(e)) from e
        try:
            if action == "approve":
                gmail_result = await asyncio.to_thread(agent2_bridge.approve, agents_dir, apply_state_file, m.draft_id)
            else:
                await asyncio.to_thread(agent2_bridge.reject, agents_dir, apply_state_file, m.draft_id)
        except agent2_bridge.DraftNotFound as e:
            raise HTTPException(404, f"draft {m.draft_id} not found in state.json") from e
        except approval_error as e:
            raise HTTPException(409, str(e)) from e
        except agent2_bridge.Agent2Unavailable as e:
            raise HTTPException(502, str(e)) from e

        events = await asyncio.to_thread(poller.refresh)
        poller.push_events(events)
        updated = poller.missions.get(mission_id)
        return {"status": "sent" if action == "approve" else "rejected", "mission": updated,
                "gmail_message_id": (gmail_result or {}).get("id")}

    @app.post("/api/missions/{mission_id}/approve")
    async def approve_mission(mission_id: str) -> dict:
        """Send the draft — the exact same agent.approval.approve_draft the email-reply APPROVE
        flow uses. Irreversible; the frontend must confirm with the owner before calling this."""
        return await _act_on_mission(mission_id, "approve")

    @app.post("/api/missions/{mission_id}/reject")
    async def reject_mission(mission_id: str) -> dict:
        """Leave the draft unsent — the exact same agent.approval.reject_draft the email-reply
        REJECT flow uses."""
        return await _act_on_mission(mission_id, "reject")

    @app.get("/api/events")
    def events(limit: int = 100) -> list[dict]:
        """Recent events (same shape as the websocket pushes), newest last."""
        return poller.events[-limit:]

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        q = poller.subscribe()
        await ws.send_json({"type": "connected", "timestamp": datetime.now().isoformat(timespec="seconds"),
                            "agent": None, "mission_id": None, "data": {"missions": len(poller.missions)}})
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    await ws.send_json(event)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "ping", "timestamp": datetime.now().isoformat(timespec="seconds"),
                                        "agent": None, "mission_id": None, "data": {}})
        except WebSocketDisconnect:
            pass
        finally:
            poller.unsubscribe(q)

    return app

