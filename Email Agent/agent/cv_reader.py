"""Locate the currently active CV and extract a candidate profile from it.

The CV is never modified. Whatever PDF/DOCX is newest in `cv_dir` is "active";
the profile is re-extracted whenever the file content changes.
"""
import hashlib
import json
import logging
from pathlib import Path

from .config import ROOT, load_prompt
from .gemini_client import Gemini
from .models import CVProfile

log = logging.getLogger(__name__)
CACHE_FILE = ROOT / ".cv_profile_cache.json"


def active_cv_files(cv_dir: Path) -> dict[str, Path]:
    """Return {"pdf": path, "docx": path} for the newest file of each type present."""
    found: dict[str, Path] = {}
    for ext in ("pdf", "docx"):
        candidates = [p for p in cv_dir.glob(f"*.{ext}") if not p.name.startswith("~$")]
        if candidates:
            found[ext] = max(candidates, key=lambda p: p.stat().st_mtime)
    if not found:
        raise FileNotFoundError(f"No .pdf or .docx CV found in {cv_dir}")
    return found


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if path.suffix.lower() == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    raise ValueError(f"Unsupported CV type: {path}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_profile(gemini: Gemini, cv_dir: Path) -> tuple[CVProfile, dict[str, Path]]:
    files = active_cv_files(cv_dir)
    # Prefer DOCX for text extraction (cleaner), PDF otherwise.
    source = files.get("docx") or files["pdf"]
    key = _digest(source)

    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if cache.get("digest") == key:
            return CVProfile.model_validate(cache["profile"]), files

    text = extract_text(source)
    if len(text.strip()) < 100:
        raise ValueError(f"Could not extract readable text from {source.name}")
    log.info("Extracting profile from active CV: %s", source.name)
    profile = gemini.structured(load_prompt("cv_profile").format(cv_text=text[:20000]), CVProfile)
    CACHE_FILE.write_text(json.dumps({"digest": key, "source": source.name, "profile": profile.model_dump()}, indent=2), encoding="utf-8")
    return profile, files
