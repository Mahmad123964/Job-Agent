"""Bridge into Agent 2's (Email Agent) own runtime to send/reject a draft.

Mission Control API is otherwise strictly read-only. `POST /api/missions/{id}/approve` and
`.../reject` are a deliberate, narrow exception to that — they import and call Agent 2's own
`agent.approval.approve_draft` / `reject_draft`, the exact same functions the email-reply
APPROVE/REJECT loop (`process_approvals`) already calls, instead of reimplementing any Gmail
send logic here. That keeps every existing guarantee (literal recipient, explicit action
required, single place a draft is ever actually sent) intact regardless of which surface
triggered it.
"""
import importlib
import logging
import sys
import types
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

_modules_lock = Lock()
_modules_cache: dict[str, types.SimpleNamespace] = {}  # agents_dir -> imported agent.* modules

_runtime_lock = Lock()
_runtime_cache: dict[str, dict[str, Any]] = {}  # agents_dir -> {cfg, gmail}


class Agent2Unavailable(RuntimeError):
    """Agent 2's runtime (config, Gmail credentials, ...) could not be loaded."""


class DraftNotFound(KeyError):
    """The given draft id has no record in Agent 2's state.json at all."""


def _import_agent_modules(agents_dir: Path) -> types.SimpleNamespace:
    """Import Agent 2's own `agent` package from its project folder, exactly as
    `python -m agent.main` would from inside that folder. Cheap and side-effect-free — it does
    not touch Gmail credentials or Agent 2's .env, so it's safe to call just to reach an
    exception class (e.g. ApprovalError) even before a Gmail client has been built."""
    key = str(agents_dir.resolve())
    with _modules_lock:
        cached = _modules_cache.get(key)
        if cached is not None:
            return cached
        if key not in sys.path:
            sys.path.insert(0, key)
        try:
            mods = types.SimpleNamespace(
                config=importlib.import_module("agent.config"),
                approval=importlib.import_module("agent.approval"),
                gmail_client=importlib.import_module("agent.gmail_client"),
                state=importlib.import_module("agent.state"),
            )
        except ImportError as e:
            raise Agent2Unavailable(f"Could not import Agent 2's package from {agents_dir}: {e}") from e
        _modules_cache[key] = mods
        return mods


def approval_error_type(agents_dir: Path):
    """agent.approval.ApprovalError, for callers that need to catch it by type."""
    return _import_agent_modules(agents_dir).approval.ApprovalError


def _runtime(agents_dir: Path) -> dict[str, Any]:
    """Agent 2's own config + a live Gmail client, built once per agents_dir and cached (an
    OAuth token refresh + Gmail service build on every approve/reject click would be wasteful)."""
    key = str(agents_dir.resolve())
    with _runtime_lock:
        cached = _runtime_cache.get(key)
        if cached is not None:
            return cached
        mods = _import_agent_modules(agents_dir)
        try:
            cfg = mods.config.load_config()
            gmail = mods.gmail_client.Gmail(cfg["gmail_credentials_file"], cfg["gmail_token_file"])
        except (Exception, SystemExit) as e:  # noqa: BLE001 - Agent 2's load_config() raises
            # SystemExit (not Exception) when a required .env value is missing; either way this
            # must surface as a clean error response, never propagate or take the process down.
            raise Agent2Unavailable(f"Could not load Agent 2's runtime: {e}") from e
        entry = {"cfg": cfg, "gmail": gmail}
        _runtime_cache[key] = entry
        return entry


def _record(state, draft_id: str) -> dict:
    rec = state.data["pending"].get(draft_id)
    if rec is None:
        raise DraftNotFound(draft_id)
    return rec


def approve(agents_dir: Path, apply_state_file: Path, draft_id: str) -> dict:
    """Send the draft. Raises Agent2Unavailable, DraftNotFound, or agent.approval.ApprovalError."""
    mods = _import_agent_modules(agents_dir)
    runtime = _runtime(agents_dir)
    state = mods.state.State(apply_state_file)
    rec = _record(state, draft_id)
    return mods.approval.approve_draft(runtime["gmail"], runtime["cfg"], state, draft_id, rec)


def reject(agents_dir: Path, apply_state_file: Path, draft_id: str) -> None:
    """Mark the draft rejected (left unsent). Same error types as `approve`."""
    mods = _import_agent_modules(agents_dir)
    runtime = _runtime(agents_dir)
    state = mods.state.State(apply_state_file)
    rec = _record(state, draft_id)
    mods.approval.reject_draft(runtime["gmail"], runtime["cfg"], state, draft_id, rec)


def reset_cache_for_tests() -> None:
    with _modules_lock:
        _modules_cache.clear()
    with _runtime_lock:
        _runtime_cache.clear()
