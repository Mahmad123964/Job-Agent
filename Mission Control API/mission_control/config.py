import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | os.PathLike | None = None) -> dict:
    cfg_path = Path(path or os.environ.get("MC_CONFIG") or ROOT / "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    agents_dir = Path(cfg.get("agents_dir", "."))
    cfg["agents_dir"] = agents_dir
    cfg["apply_state_file"] = agents_dir / cfg.get("apply_state_file", "state.json")
    cfg["scout_state_file"] = agents_dir / cfg.get("scout_state_file", "scout_state.json")
    cfg["events_log"] = ROOT / cfg.get("events_log", "events.jsonl")
    cfg.setdefault("active_window_seconds", 30)
    cfg.setdefault("poll_interval_seconds", 2)
    cfg.setdefault("cors_origins", [])
    # Local dev: the frontend's port varies (Vite auto-increments when 3000 is taken), so match
    # any localhost/127.0.0.1/[::1] origin by regex instead of hardcoding ports. For a real
    # deployment, set cors_origin_regex to null in config.yaml and list exact origins in
    # cors_origins instead — this pattern is deliberately permissive and local-only.
    cfg.setdefault("cors_origin_regex", r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
    cfg["owner_email"] = _read_owner_email(agents_dir)
    return cfg


def _read_owner_email(agents_dir: Path) -> str | None:
    """Read owner_email from the Email Agent's own config.yaml (read-only). Used to build
    Gmail deep links (?authuser=) that open in the right Google account. None if unavailable
    — callers must degrade gracefully rather than hardcode an address."""
    agent_cfg_path = agents_dir / "config.yaml"
    try:
        with open(agent_cfg_path, encoding="utf-8") as f:
            agent_cfg = yaml.safe_load(f) or {}
        email = agent_cfg.get("owner_email")
        return email.strip() if isinstance(email, str) and email.strip() else None
    except (OSError, yaml.YAMLError):
        return None
