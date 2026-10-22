"""Fetch a single job posting page politely and pull out what is literally on it.

Rules this module enforces (spec: Link Resolver Agent):
- Only the URL handed in is fetched (plus that host's robots.txt). No crawling, no
  follow-up lookups, no third-party enrichment/scraping services, no browser extensions.
- robots.txt is honoured for the configured user agent.
- One request per domain at most every `min_seconds_between_requests`.
- Optional Playwright fallback renders the same single URL in a headless browser when the
  plain HTTP response has no usable text (JS-only pages). Nothing else is automated.
"""
import json
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, unquote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from .gmail_client import html_to_text

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobAgentLinkResolver/1.0"
BLOCKED_STATUSES = {401, 403, 429, 999}


@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int = 0
    html: str = ""
    text: str = ""
    # From a JSON-LD JobPosting block when the page has one (most job boards do).
    ld_title: Optional[str] = None
    ld_company: Optional[str] = None
    ld_description: Optional[str] = None
    emails: list[str] = field(default_factory=list)
    fetched_with: str = "requests"
    blocked_reason: Optional[str] = None  # set when nothing could be fetched

    @property
    def ok(self) -> bool:
        return self.blocked_reason is None and bool(self.text.strip())

    @property
    def login_wall(self) -> bool:
        u = self.final_url.lower()
        return any(k in u for k in ("/authwall", "/login", "/uas/login", "checkpoint/challenge"))


def find_emails(html: str, text: str, ignore_prefixes: list[str]) -> list[str]:
    """Every address literally present in the page (html incl. mailto: links + visible text)."""
    found: list[str] = []
    for src in (html, text):
        for m in EMAIL_RE.findall(src):
            e = m.strip(".").lower()
            local = e.split("@", 1)[0]
            if any(local.startswith(p) or local == p for p in ignore_prefixes):
                continue
            if e.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):  # image@2x.png false positives
                continue
            if e not in found:
                found.append(e)
    return found


def parse_job_posting_ld(soup: BeautifulSoup) -> dict:
    """Return {title, company, description} from a schema.org JobPosting block, if present."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        for node in _ld_nodes(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" not in types:
                continue
            org = node.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org
            desc = node.get("description") or ""
            return {
                "title": node.get("title") or None,
                "company": company or None,
                "description": html_to_text(desc) if "<" in desc else desc,
            }
    return {}


def _ld_nodes(data):
    if isinstance(data, list):
        for d in data:
            yield from _ld_nodes(d)
    elif isinstance(data, dict):
        yield data
        for d in data.get("@graph", []) or []:
            yield from _ld_nodes(d)


def _normalize_path(url: str) -> str:
    """URL -> the path+query robots.txt rules are matched against (percent-encoded,
    fragment stripped), the same normalisation `RobotFileParser.can_fetch` applies."""
    parsed = urlparse(unquote(url))
    path = urlunparse(("", "", parsed.path, parsed.params, parsed.query, ""))
    return quote(path) or "/"


def _matching_entry(rp: urllib.robotparser.RobotFileParser, ua: str):
    """The Entry (User-agent block) that applies to `ua`, mirroring the selection
    RobotFileParser.can_fetch() itself uses: first named block that matches, else the
    catch-all `User-agent: *` block, else None (no rule at all -> allowed)."""
    for entry in rp.entries:
        if entry.applies_to(ua):
            return entry
    return rp.default_entry


def robots_can_fetch(rp: urllib.robotparser.RobotFileParser, ua: str, url: str) -> bool:
    """Spec-correct replacement for `RobotFileParser.can_fetch()`.

    The stdlib implementation resolves conflicting rules by *file order* — the first
    matching Allow/Disallow line in the block wins (`Entry.allowance` just returns on the
    first `RuleLine.applies_to()` hit). Google's actual robots.txt spec instead says the
    *longest matching path* wins regardless of order, with equally-specific rules resolved
    in favour of the least restrictive (Allow) one:
    https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt#order-of-precedence-for-group-member-lines

    This matters in practice: Indeed's robots.txt lists a blanket `Allow: /` before the
    more specific `Disallow: /viewjob` in the same block, so stdlib's `can_fetch()`
    incorrectly reports /viewjob as allowed. Re-evaluating the same parsed rulelines by
    longest-match-wins instead fixes that without needing a different robots.txt parser.
    """
    if rp.disallow_all:
        return False
    if rp.allow_all:
        return True
    entry = _matching_entry(rp, ua)
    if entry is None:
        return True  # no rule at all applies to this agent
    path = _normalize_path(url)
    best_len, best_allow = -1, True
    for line in entry.rulelines:
        if not line.applies_to(path):
            continue
        if len(line.path) > best_len or (len(line.path) == best_len and line.allowance):
            best_len, best_allow = len(line.path), line.allowance
    return best_allow


def page_text(soup: BeautifulSoup) -> str:
    for t in soup(["script", "style", "noscript", "svg", "iframe"]):
        t.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class PageFetcher:
    def __init__(self, cfg: dict):
        self.timeout = float(cfg.get("timeout_seconds", 20))
        self.min_gap = float(cfg.get("min_seconds_between_requests", 5))
        self.ua = cfg.get("user_agent") or DEFAULT_UA
        self.respect_robots = bool(cfg.get("respect_robots_txt", True))
        self.playwright_fallback = bool(cfg.get("playwright_fallback", False))
        self.min_text_chars = int(cfg.get("min_text_chars", 300))
        self.ignore_email_prefixes = [p.lower() for p in cfg.get("ignore_email_prefixes", [])]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.ua, "Accept-Language": "en-US,en;q=0.9"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    # ---- politeness ---------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urlparse(url).netloc.lower()
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
            try:
                self._throttle(host)
                resp = self.session.get(robots_url, timeout=self.timeout)
                if resp.status_code >= 400:
                    self._robots[host] = None  # no robots.txt -> nothing disallowed
                else:
                    rp.parse(resp.text.splitlines())
                    self._robots[host] = rp
            except requests.RequestException as e:
                log.warning("robots.txt unreachable for %s (%s); treating as allowed", host, e)
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else robots_can_fetch(rp, self.ua, url)

    def _throttle(self, host: str) -> None:
        wait = self._last_hit.get(host, 0) + self.min_gap - time.monotonic()
        if wait > 0:
            log.debug("rate limit: sleeping %.1fs before hitting %s", wait, host)
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # ---- fetching -----------------------------------------------------
    def fetch(self, url: str) -> Page:
        page = Page(url=url)
        if not self.allowed(url):
            page.blocked_reason = "disallowed by robots.txt"
            log.info("Skipping fetch (robots.txt): %s", url)
            return page

        host = urlparse(url).netloc.lower()
        try:
            self._throttle(host)
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            page.status, page.final_url, page.html = resp.status_code, resp.url, resp.text
        except requests.RequestException as e:
            page.blocked_reason = f"request failed: {e.__class__.__name__}"
            log.warning("Fetch failed for %s: %s", url, e)
            return page

        self._parse(page)
        thin = len(page.text) < self.min_text_chars and not page.ld_description
        if (page.status in BLOCKED_STATUSES or thin) and self.playwright_fallback and not page.login_wall:
            log.info("Plain fetch gave status %s / %d chars; rendering %s with Playwright", page.status, len(page.text), url)
            self._throttle(host)
            self._render(page)

        if page.status in BLOCKED_STATUSES and len(page.text) < self.min_text_chars and not page.ld_description:
            page.blocked_reason = f"HTTP {page.status}"
        elif page.status >= 400 and not page.ld_description:
            page.blocked_reason = f"HTTP {page.status}"
        elif page.login_wall and not page.ld_description:
            page.blocked_reason = "login wall"
        elif not page.text.strip():
            page.blocked_reason = "empty page"
        return page

    def _parse(self, page: Page) -> None:
        soup = BeautifulSoup(page.html or "", "html.parser")
        ld = parse_job_posting_ld(soup)
        page.ld_title, page.ld_company, page.ld_description = ld.get("title"), ld.get("company"), ld.get("description")
        page.text = page_text(soup)
        page.emails = find_emails(page.html or "", page.text, self.ignore_email_prefixes)

    def _render(self, page: Page) -> None:
        """Render the same URL in headless Chromium (optional dependency)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright_fallback is on but playwright is not installed (pip install playwright && playwright install chromium)")
            return
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(user_agent=self.ua)
                    tab = ctx.new_page()
                    resp = tab.goto(page.url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    tab.wait_for_timeout(1500)
                    page.status = resp.status if resp else page.status
                    page.final_url, page.html = tab.url, tab.content()
                    page.fetched_with = "playwright"
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001 - rendering is best-effort
            log.warning("Playwright render failed for %s: %s", page.url, str(e)[:160])
            return
        self._parse(page)
