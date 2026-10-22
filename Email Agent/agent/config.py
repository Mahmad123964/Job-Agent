import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")
    cfg["gmail_credentials_file"] = ROOT / os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
    cfg["gmail_token_file"] = ROOT / os.environ.get("GMAIL_TOKEN_FILE", "token.json")
    cfg["cv_dir"] = ROOT / cfg.get("cv_dir", "cv")
    cfg["state_file"] = ROOT / cfg.get("state_file", "state.json")
    if not cfg["gemini_api_key"]:
        raise SystemExit("GEMINI_API_KEY is not set (see .env.example)")
    return cfg


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def load_template(name: str) -> str:
    return (ROOT / "templates" / name).read_text(encoding="utf-8")
