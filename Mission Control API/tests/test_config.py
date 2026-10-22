import yaml

from mission_control.config import load_config


def _write_mc_config(tmp_path, agents_dir):
    p = tmp_path / "mc_config.yaml"
    p.write_text(yaml.safe_dump({"agents_dir": str(agents_dir)}), encoding="utf-8")
    return p


def test_owner_email_read_from_agents_config(tmp_path):
    agents_dir = tmp_path / "Email Agent"
    agents_dir.mkdir()
    (agents_dir / "config.yaml").write_text(yaml.safe_dump({"owner_email": "qanitabokhari5@gmail.com"}), encoding="utf-8")

    cfg = load_config(_write_mc_config(tmp_path, agents_dir))
    assert cfg["owner_email"] == "qanitabokhari5@gmail.com"


def test_owner_email_none_when_agents_config_missing(tmp_path):
    agents_dir = tmp_path / "no-such-dir"
    cfg = load_config(_write_mc_config(tmp_path, agents_dir))
    assert cfg["owner_email"] is None


def test_owner_email_none_when_key_absent(tmp_path):
    agents_dir = tmp_path / "Email Agent"
    agents_dir.mkdir()
    (agents_dir / "config.yaml").write_text(yaml.safe_dump({"gemini_model": "x"}), encoding="utf-8")
    cfg = load_config(_write_mc_config(tmp_path, agents_dir))
    assert cfg["owner_email"] is None
