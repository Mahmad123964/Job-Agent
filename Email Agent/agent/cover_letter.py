import logging

from .config import load_prompt, load_template
from .gemini_client import Gemini, GeminiUnavailable
from .models import CoverLetterResult, CVProfile, JDResult

log = logging.getLogger(__name__)


def _join(items: list[str], last: str = "and") -> str:
    items = [i.strip() for i in items if i.strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def fallback_letter(profile: CVProfile, role_title: str | None) -> str:
    """Default template, populated only from the active CV (never hardcoded)."""
    return load_template("default_cover_letter.txt").format(
        role_title=role_title or profile.current_role,
        core_skill_areas=_join(profile.core_skill_areas[:3]),
        key_skills=_join(profile.key_skills[:3]),
        candidate_name=profile.name,
        candidate_role=profile.current_role,
    ).strip() + "\n"


def tailored_letter(gemini: Gemini, jd: JDResult, profile: CVProfile, recruiter_name: str) -> str | None:
    prompt = load_prompt("cover_letter").format(
        role_title=jd.role_title or "(not stated)",
        company=jd.company or "(not stated)",
        summary=jd.summary,
        responsibilities="\n".join(f"- {r}" for r in jd.responsibilities) or "- (none stated)",
        requirements="\n".join(f"- {r}" for r in jd.requirements) or "- (none stated)",
        name=profile.name,
        current_role=profile.current_role,
        core_skill_areas=", ".join(profile.core_skill_areas),
        key_skills=", ".join(profile.key_skills),
        experience_summary=profile.experience_summary,
        recruiter_name=recruiter_name or "(unknown)",
    )
    result = gemini.structured(prompt, CoverLetterResult, temperature=0.4)
    if not result.confident or len(result.letter.strip()) < 120:
        return None
    return result.letter.strip() + "\n"


def build_letter(gemini: Gemini, jd: JDResult, profile: CVProfile, recruiter_name: str, scored_role: str | None) -> tuple[str, str]:
    """Returns (letter_text, mode) where mode is 'tailored' or 'default'."""
    if jd.usable:
        try:
            letter = tailored_letter(gemini, jd, profile, recruiter_name)
        except GeminiUnavailable as e:
            log.warning("Tailored letter unavailable (Gemini failed after retries); using default template: %s", e)
            letter = None
        else:
            if letter:
                return letter, "tailored"
            log.info("Gemini not confident in tailored letter; using default template")
    return fallback_letter(profile, jd.role_title or scored_role), "default"
