"""Local, free, offline-capable job classifier built on Hugging Face models.

No paid LLM API is involved. Two models, both downloaded once from the HF Hub
and cached on disk (~/.cache/huggingface), then loaded once per process:

* HF_RELEVANCE_MODEL (default sentence-transformers/all-MiniLM-L6-v2, ~90 MB)
  embeds title+description and compares cosine similarity against a set of
  SQA reference phrases -> is_sqa_relevant + relevance_score.
* HF_ZEROSHOT_MODEL (default facebook/bart-large-mnli, ~1.6 GB) does zero-shot
  classification with candidate labels -> seniority, is_remote, is_worldwide.

Two fields are deliberately rule-based (cheaper and more reliable for a very
specific question):

* pakistan_eligible: regex checks for location / work-authorisation exclusions.
* eligible_region / eligible_country: geo.py rules (country table + restriction
  phrases); the zero-shot model is only consulted when rules find nothing and
  the description is long enough to be worth it.
* required_skills: keyword match against a maintained SQA tooling list.

Performance note: bart-large-mnli costs roughly 0.5-2 s per zero-shot call on a
laptop CPU. To keep polls fast we (a) only classify jobs that already passed
the keyword filter and dedup, (b) truncate text to HF_MAX_TEXT_CHARS, and
(c) short-circuit with title/location rules when they're unambiguous
("Senior QA Engineer" does not need a 400M-parameter model to be called senior).

If `transformers` / `torch` are not installed, or a model fails to load, the
classifier logs a warning once and falls back to pure heuristics so ingestion
keeps working — `classifier_backend()` reports which mode is active.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional

from .config import get_settings
from .geo import Eligibility, extract_eligibility
from .normalizer import NormalizedJob

logger = logging.getLogger("sqa_job_agent.classifier")

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #

# Phrases the relevance embedder compares against. Broad on purpose: the
# max similarity across the list is used, so adding a phrase can only widen recall.
SQA_REFERENCE_PHRASES: List[str] = [
    "QA engineer",
    "software quality assurance engineer",
    "software tester",
    "test automation engineer",
    "quality assurance analyst",
    "SDET software development engineer in test",
    "manual and automated software testing",
    "writing test cases and executing test plans",
    "API testing and regression testing",
    "QA automation with Selenium, Cypress or Playwright",
]

# Raw cosine similarities from MiniLM are compressed into a narrow band
# (unrelated ~0.1-0.25, strongly related ~0.55-0.75). These two constants
# stretch that band onto 0-1 so relevance_score is meaningful for scoring.
_SIM_FLOOR = 0.15
_SIM_CEIL = 0.70

# Keyword-in-title evidence is strong enough to override a weak embedding.
_SQA_TITLE_PATTERN = re.compile(
    r"\b(qa|sqa|sdet|quality assurance|quality engineer|test(?:ing)? engineer|software tester|"
    r"test automation|automation (?:tester|engineer)|developer in test|engineer in test|qa (?:analyst|lead|manager|automation))\b",
    re.IGNORECASE,
)

# Zero-shot candidate labels. Keys are the values we store; values are the
# natural-language hypotheses bart-large-mnli scores.
SENIORITY_LABELS: Dict[str, str] = {
    "junior": "junior or entry-level position",
    "mid": "mid-level position with a few years of experience",
    "senior": "senior position",
    "lead": "lead, principal, staff or manager position",
}
REMOTE_LABELS: Dict[bool, str] = {
    True: "fully remote job",
    False: "on-site or hybrid job requiring office presence",
}
WORLDWIDE_LABELS: Dict[bool, str] = {
    True: "open to applicants from anywhere in the world",
    False: "restricted to applicants in specific countries or regions",
}
# Below this top-label probability we return "unknown" rather than guess.
_ZERO_SHOT_MIN_CONFIDENCE = 0.40

# eligible_region fallback (geo.py rules found nothing). Only worth a
# bart-large-mnli call when there is enough description to say anything.
REGION_LABELS: Dict[str, str] = {
    "Worldwide": "open to applicants from anywhere in the world",
    "Americas": "restricted to applicants in North or South America",
    "Europe": "restricted to applicants in Europe",
    "Asia-Pacific": "restricted to applicants in Asia, the Middle East or the Pacific",
    "Africa": "restricted to applicants in Africa",
}
_REGION_MIN_CONFIDENCE = 0.50
_REGION_MIN_DESCRIPTION_CHARS = 300

# Title rules that make a zero-shot call unnecessary.
_TITLE_SENIORITY_RULES = [
    ("lead", re.compile(r"\b(lead|principal|staff|head of|manager|director|architect)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|snr)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]level|intern(?:ship)?|graduate|trainee|associate)\b", re.I)),
    ("mid", re.compile(r"\b(mid[- ]level|intermediate|mid)\b", re.I)),
]

# --- pakistan_eligible rules -------------------------------------------------
_WORLDWIDE_TERMS = (
    "worldwide", "anywhere", "global", "work from anywhere", "any country", "any location",
    "all countries", "no location restriction", "international",
)
_PAKISTAN_FRIENDLY_TERMS = ("pakistan", "south asia", "asia", "apac", "asia pacific", "middle east")

# Region tokens used inside the exclusion patterns below.
_REGION = (
    r"(?:the\s+)?(?:us|usa|u\.s\.a?\.?|united states|america|north america|canada|"
    r"uk|u\.k\.|united kingdom|great britain|england|eu|e\.u\.|europe|european union|emea|"
    r"latam|latin america|brazil|mexico|australia|new zealand|germany|france|spain|"
    r"netherlands|poland|portugal|ireland|italy|sweden|denmark|norway|finland|switzerland|"
    r"austria|belgium|india|japan|singapore|israel|ukraine|argentina|colombia|philippines)"
)
_EXCLUSION_PATTERNS = [
    re.compile(_REGION + r"[\s-]*(?:only|based|residents?|citizens?)\b", re.I),
    re.compile(r"\bonly\s+(?:for\s+|open\s+to\s+)?(?:candidates|applicants|people)?\s*(?:in|from|within)?\s*" + _REGION + r"\b", re.I),
    re.compile(
        r"\bmust\s+(?:be|reside|live|currently\s+reside|be\s+located|be\s+based|be\s+physically\s+located)\b"
        r"[^.\n]{0,40}\b(?:in|within)\s+" + _REGION + r"\b",
        re.I,
    ),
    re.compile(r"\b(?:based|located|residing|living)\s+(?:in|within)\s+" + _REGION + r"\b", re.I),
    re.compile(
        r"\b(?:authori[sz]ed|eligible|legally\s+(?:able|allowed|permitted)|right|permission)\s+to\s+work\s+(?:in|within)\s+"
        + _REGION + r"\b",
        re.I,
    ),
    re.compile(r"\b(?:us|uk|eu|canadian|australian)\s+work\s+(?:authori[sz]ation|permit|visa)\b", re.I),
    re.compile(r"\b(?:security\s+clearance|green\s+card|citizenship\s+required|citizens?\s+only)\b", re.I),
    re.compile(r"\bcandidates\s+(?:in|from)\s+" + _REGION + r"\s+only\b", re.I),
]
# Location strings that name a restricted place (word-boundary match on the
# location field only — the description is handled by the patterns above).
_RESTRICTED_LOCATION = re.compile(r"\b" + _REGION + r"\b", re.I)

# --- required_skills keyword list -------------------------------------------
# canonical name -> regex alternatives (word-boundary, case-insensitive).
SKILL_PATTERNS: Dict[str, str] = {
    "Selenium": r"selenium",
    "Cypress": r"cypress",
    "Playwright": r"playwright",
    "Puppeteer": r"puppeteer",
    "WebdriverIO": r"webdriver\s?io|wdio",
    "Appium": r"appium",
    "Espresso": r"espresso",
    "XCUITest": r"xcuitest|xctest",
    "Detox": r"detox",
    "Postman": r"postman",
    "SoapUI": r"soap\s?ui",
    "REST Assured": r"rest[\s-]?assured",
    "API Testing": r"api\s+test(?:ing)?|rest(?:ful)?\s+api",
    "Manual Testing": r"manual\s+test(?:ing|er)?",
    "Automation Testing": r"(?:test|qa)\s+automation|automation\s+test(?:ing)?|automated\s+test(?:ing|s)?",
    "Regression Testing": r"regression\s+test(?:ing)?",
    "Performance Testing": r"performance\s+test(?:ing)?|load\s+test(?:ing)?|stress\s+test(?:ing)?",
    "JMeter": r"jmeter",
    "LoadRunner": r"loadrunner",
    "k6": r"\bk6\b",
    "Gatling": r"gatling",
    "Locust": r"locust",
    "Security Testing": r"security\s+test(?:ing)?|penetration\s+test(?:ing)?|owasp",
    "Accessibility Testing": r"accessibility\s+test(?:ing)?|wcag|a11y",
    "Mobile Testing": r"mobile\s+test(?:ing)?|ios\s+and\s+android|android\s+and\s+ios",
    "BDD / Cucumber": r"cucumber|gherkin|\bbdd\b|specflow|behave",
    "TestNG": r"testng",
    "JUnit": r"junit",
    "PyTest": r"pytest",
    "Robot Framework": r"robot\s+framework",
    "Katalon": r"katalon",
    "TestCafe": r"testcafe",
    "Jest": r"\bjest\b",
    "Mocha": r"\bmocha\b",
    "JIRA": r"\bjira\b",
    "TestRail": r"testrail",
    "Zephyr": r"zephyr",
    "Xray": r"\bxray\b",
    "qTest": r"\bqtest\b",
    "SQL": r"\bsql\b|postgres|mysql",
    "Python": r"\bpython\b",
    "Java": r"\bjava\b(?!script)",
    "JavaScript": r"javascript|\bjs\b",
    "TypeScript": r"typescript",
    "C#": r"\bc#|\.net\b",
    "Git": r"\bgit\b|github|gitlab|bitbucket",
    "CI/CD": r"\bci/?cd\b|continuous\s+integration|continuous\s+delivery",
    "Jenkins": r"jenkins",
    "GitHub Actions": r"github\s+actions",
    "GitLab CI": r"gitlab\s+ci",
    "Docker": r"docker",
    "Kubernetes": r"kubernetes|\bk8s\b",
    "AWS": r"\baws\b|amazon\s+web\s+services",
    "Azure": r"\bazure\b",
    "GCP": r"\bgcp\b|google\s+cloud",
    "BrowserStack": r"browserstack",
    "Sauce Labs": r"sauce\s?labs",
    "Charles Proxy": r"charles\s+proxy",
    "Agile / Scrum": r"\bagile\b|\bscrum\b|\bkanban\b",
    "ISTQB": r"istqb",
}
_COMPILED_SKILLS = [(name, re.compile(pattern, re.I)) for name, pattern in SKILL_PATTERNS.items()]


# --------------------------------------------------------------------------- #
# Model loading (once per process)
# --------------------------------------------------------------------------- #

class _Models:
    """Holds the loaded HF models plus the pre-computed reference embeddings."""

    def __init__(self) -> None:
        settings = get_settings()
        # Imported lazily so the rest of the app doesn't pay the torch import
        # cost (or fail) when models are unavailable.
        import torch
        from sentence_transformers import SentenceTransformer
        from transformers import pipeline

        device = settings.HF_DEVICE
        if device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("HF_DEVICE=%s requested but CUDA is not available; using cpu", device)
            device = "cpu"
        self.device = device

        logger.info("Loading relevance model %s on %s", settings.HF_RELEVANCE_MODEL, device)
        self.embedder = SentenceTransformer(settings.HF_RELEVANCE_MODEL, device=device)
        self.reference = self.embedder.encode(
            SQA_REFERENCE_PHRASES, normalize_embeddings=True, convert_to_numpy=True
        )

        logger.info("Loading zero-shot model %s on %s", settings.HF_ZEROSHOT_MODEL, device)
        # transformers pipelines take -1 for CPU or a CUDA ordinal.
        pipe_device = -1 if device == "cpu" else int(device.split(":")[1]) if ":" in device else 0
        self.zero_shot = pipeline(
            "zero-shot-classification", model=settings.HF_ZEROSHOT_MODEL, device=pipe_device
        )


_models: Optional[_Models] = None
_models_failed = False
_models_lock = threading.Lock()


def _get_models() -> Optional[_Models]:
    """Return the cached model bundle, loading it on first use. None => use rules."""
    global _models, _models_failed
    if _models is not None or _models_failed:
        return _models
    with _models_lock:
        if _models is None and not _models_failed:
            try:
                _models = _Models()
            except Exception as exc:  # ImportError, OSError (download), RuntimeError...
                _models_failed = True
                logger.warning(
                    "Hugging Face models unavailable (%s: %s); classifier is running in "
                    "rule-based fallback mode. Install transformers/torch/sentence-transformers "
                    "and check network access for the first download.",
                    type(exc).__name__, exc,
                )
    return _models


def preload_models() -> str:
    """Load models eagerly (call at startup). Returns the active backend name."""
    _get_models()
    return classifier_backend()


def classifier_backend() -> str:
    return "huggingface" if _models is not None else "rules"


# --------------------------------------------------------------------------- #
# Field-level classifiers
# --------------------------------------------------------------------------- #

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _relevance(models: Optional[_Models], title: str, text: str) -> tuple[bool, float]:
    settings = get_settings()
    title_hit = bool(_SQA_TITLE_PATTERN.search(title))

    if models is None:
        # Heuristic fallback: title hit or a couple of body keywords.
        body_hits = len(re.findall(r"\b(qa|test(?:ing)?|quality assurance|sdet)\b", text, re.I))
        score = 0.85 if title_hit else _clamp01(body_hits / 8)
        return score >= 0.5, score

    vector = models.embedder.encode(text, normalize_embeddings=True, convert_to_numpy=True)
    similarity = float((models.reference @ vector).max())
    score = _clamp01((similarity - _SIM_FLOOR) / (_SIM_CEIL - _SIM_FLOOR))
    if title_hit:
        # An explicit QA/SDET title is strong evidence regardless of how a
        # short or boilerplate-heavy description happens to embed.
        score = max(score, 0.8)
    is_relevant = similarity >= settings.HF_RELEVANCE_THRESHOLD or title_hit
    return is_relevant, round(score, 3)


def description_relevance(description: str) -> Optional[float]:
    """Calibrated 0-1 relevance of the DESCRIPTION alone (no title influence).
    Used by the scheduler's secondary recall path. None when no embedder is
    loaded — the rule-based fallback has no way to judge a description."""
    models = _get_models()
    text = (description or "").strip()
    if models is None or not text:
        return None
    vector = models.embedder.encode(text[: get_settings().HF_MAX_TEXT_CHARS * 2], normalize_embeddings=True, convert_to_numpy=True)
    similarity = float((models.reference @ vector).max())
    return round(_clamp01((similarity - _SIM_FLOOR) / (_SIM_CEIL - _SIM_FLOOR)), 3)


def _zero_shot(models: _Models, text: str, labels: Dict[Any, str]) -> tuple[Optional[Any], float]:
    """Run one zero-shot call; return (winning key, confidence)."""
    hypotheses = list(labels.values())
    result = models.zero_shot(text, candidate_labels=hypotheses, multi_label=False)
    top_label, top_score = result["labels"][0], float(result["scores"][0])
    for key, hypothesis in labels.items():
        if hypothesis == top_label:
            return key, top_score
    return None, top_score


def _seniority(models: Optional[_Models], title: str, text: str) -> str:
    for level, pattern in _TITLE_SENIORITY_RULES:
        if pattern.search(title):
            return level
    # Description regex next (cheap); BART only when both title and text are silent.
    for level, pattern in _TITLE_SENIORITY_RULES:
        if pattern.search(text):
            return level
    if models is None:
        return "unknown"
    level, confidence = _zero_shot(models, text, SENIORITY_LABELS)
    return level if level and confidence >= _ZERO_SHOT_MIN_CONFIDENCE else "unknown"


def _is_remote(models: Optional[_Models], location: str, text: str) -> bool:
    loc = location.lower()
    if any(term in loc for term in ("remote", "work from home", "wfh", "anywhere", "worldwide")):
        return True
    if any(term in loc for term in ("on-site", "onsite", "on site", "hybrid", "in-office")):
        return False
    # Text regex before the model; BART only when location and text are both silent.
    if re.search(r"\b(fully remote|100% remote|remote[- ]first|work from home|remote position|remote role)\b", text, re.I):
        return True
    if re.search(r"\b(on-?site|in[- ]office|hybrid)\b", text, re.I):
        return False
    if models is None:
        return False
    remote, confidence = _zero_shot(models, f"Location: {location}. {text}", REMOTE_LABELS)
    return bool(remote) if confidence >= _ZERO_SHOT_MIN_CONFIDENCE else False


def _is_worldwide(models: Optional[_Models], location: str, text: str) -> bool:
    loc = location.lower()
    if any(term in loc for term in _WORLDWIDE_TERMS):
        return True
    if _RESTRICTED_LOCATION.search(loc):
        return False
    if models is None:
        return any(term in text.lower() for term in _WORLDWIDE_TERMS)
    worldwide, confidence = _zero_shot(models, f"Location: {location}. {text}", WORLDWIDE_LABELS)
    return bool(worldwide) if confidence >= _ZERO_SHOT_MIN_CONFIDENCE else False


def pakistan_eligible(location: str, description: str) -> bool:
    """Rule-based: can someone living in Pakistan (no US/EU work rights) apply?

    Strategy: explicit inclusion in the location wins; then any exclusion
    phrase in the text rules the job out; then a location naming another
    specific country/region rules it out; a plain "Remote" with no restriction
    text is assumed eligible.
    """
    loc = (location or "").lower()
    text = f"{location}\n{description or ''}"

    if any(term in loc for term in _PAKISTAN_FRIENDLY_TERMS + _WORLDWIDE_TERMS):
        return True
    for pattern in _EXCLUSION_PATTERNS:
        if pattern.search(text):
            return False
    if _RESTRICTED_LOCATION.search(loc):
        return False
    return True


def eligibility(models: Optional[_Models], location: str, description: str, title: str = "") -> Eligibility:
    """Rules only (geo.py). The zero-shot fallback that used to run here was
    the source of the "Worldwide" mislabels: for any posting whose location
    was just "Remote", BART picked "open to applicants from anywhere" for
    almost every US-market listing. The rule now is strict — explicit
    unrestricted phrasing => Worldwide, any country/state restriction => that
    country, otherwise Unclear. Ambiguity is never resolved to Worldwide."""
    return extract_eligibility(location, description, title)


# --- resume match (feature 4) ---------------------------------------------
# Resume-vs-job cosine similarity from MiniLM sits in a narrower band than the
# title-vs-reference-phrase one: unrelated ~0.15-0.25, a good fit ~0.5-0.65.
_RESUME_SIM_FLOOR = 0.20
_RESUME_SIM_CEIL = 0.65
_resume_embedding_cache: Dict[int, Any] = {}


def _resume_embedding(models: _Models, resume_text: str):
    """Embed the resume once per distinct text (the poll loop calls this per job)."""
    key = hash(resume_text)
    if key not in _resume_embedding_cache:
        _resume_embedding_cache.clear()  # only ever one resume at a time
        _resume_embedding_cache[key] = models.embedder.encode(
            resume_text[: get_settings().HF_MAX_TEXT_CHARS * 4], normalize_embeddings=True, convert_to_numpy=True
        )
    return _resume_embedding_cache[key]


def resume_match_score(resume_text: Optional[str], title: str, description: str, skills: List[str]) -> Optional[int]:
    """0-100 similarity between the user's resume and a job, or None when there
    is no resume (or no embedder — the rule-based fallback can't do this)."""
    resume_text = (resume_text or "").strip()
    if not resume_text:
        return None
    models = _get_models()
    if models is None:
        return None
    body = (description or "").strip()
    job_text = f"{title}. {body[: get_settings().HF_MAX_TEXT_CHARS * 2]}" if body else f"{title}. Skills: {', '.join(skills)}"
    vector = models.embedder.encode(job_text, normalize_embeddings=True, convert_to_numpy=True)
    similarity = float(_resume_embedding(models, resume_text) @ vector)
    return round(100 * _clamp01((similarity - _RESUME_SIM_FLOOR) / (_RESUME_SIM_CEIL - _RESUME_SIM_FLOOR)))


def extract_skills(text: str) -> List[str]:
    """Keyword extraction against SKILL_PATTERNS, in list order (stable for UI tags)."""
    return [name for name, pattern in _COMPILED_SKILLS if pattern.search(text)]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def classify(job: NormalizedJob, resume_text: Optional[str] = None) -> Dict[str, Any]:
    """Classify one normalized job. Returns a plain dict consumed by scoring.py
    and the scheduler:

        is_sqa_relevant: bool
        relevance_score: float   # 0-1
        seniority: junior | mid | senior | lead | unknown
        is_remote: bool
        is_worldwide: bool
        pakistan_eligible: bool
        eligible_region: Worldwide | Americas | Europe | Asia-Pacific | Africa | Specific country | Unclear
        eligible_country: str | None   # only with "Specific country"
        required_skills: list[str]
        resume_match_score: int | None   # 0-100; None when no resume is set
        backend: "huggingface" | "rules"
    """
    settings = get_settings()
    models = _get_models()

    description = job.description[: settings.HF_MAX_TEXT_CHARS]
    text = f"{job.title}. {description}".strip()
    skills_text = f"{job.title}\n{job.description}\n{' '.join(job.tags)}"

    is_relevant, relevance_score = _relevance(models, job.title, text)
    where = eligibility(models, job.location, job.description, job.title)
    skills = extract_skills(skills_text)
    # Keep the boolean flags consistent with the strict region result: a job
    # restricted to one country/region is neither "worldwide" nor eligible from
    # Pakistan unless that place is Pakistan / Asia-Pacific / explicitly worldwide.
    worldwide = where.region == "Worldwide"
    pk_eligible = pakistan_eligible(job.location, job.description)
    if where.region == "Specific country":
        pk_eligible = where.country == "Pakistan"
    elif where.region in ("Americas", "Europe", "Africa", "Unclear"):
        pk_eligible = False  # Unclear = no positive evidence; never assume eligibility
    return {
        "is_sqa_relevant": is_relevant,
        "relevance_score": relevance_score,
        "seniority": _seniority(models, job.title, text),
        "is_remote": _is_remote(models, job.location, text),
        "is_worldwide": worldwide,
        "pakistan_eligible": pk_eligible,
        "eligible_region": where.region,
        "eligible_country": where.country,
        "required_skills": skills,
        "resume_match_score": resume_match_score(resume_text, job.title, job.description, skills),
        "backend": classifier_backend(),
    }
