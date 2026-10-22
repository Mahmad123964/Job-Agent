from .arbeitnow import ArbeitnowAdapter
from .ashby import ASHBY_BOARDS, DeelAdapter, OysterHRAdapter
from .greenhouse import GREENHOUSE_BOARDS, RemoteComAdapter
from .himalayas import HimalayasAdapter
from .jobicy import JobicyAdapter
from .jsearch import JSearchAdapter
from .lever import LEVER_BOARDS
from .landing_jobs import LandingJobsAdapter
from .remoteok import RemoteOKAdapter
from .remotive import RemotiveAdapter
from .rss_sources import (
    AuthenticJobsAdapter,
    DribbbleAdapter,
    JobspressoAdapter,
    NodeskAdapter,
    PangianAdapter,
    RemoteFirstJobsAdapter,
    RemoteIOAdapter,
    StartupJobsAdapter,
    WeWorkRemotelyAdapter,
)
from .working_nomads import WorkingNomadsAdapter

# Deliberately NOT included:
#   - LinkedIn / Indeed / Glassdoor: no public API and their Terms of Service
#     prohibit automated access (scraping or browser automation alike). Their
#     listings are reachable only through the licensed JSearch adapter below.
#   - Wellfound (ex-AngelList Talent): no public job-search API is offered and
#     its ToS likewise forbids bots/scrapers, so it is skipped rather than
#     automated. Revisit if Wellfound publishes an official API or a licensed
#     aggregator (like JSearch) begins re-indexing it.
#
# Unregistered (adapter files kept so they can be re-enabled by adding them back here):
#   - PangianAdapter  : feed URL returns HTTP 404 (audit, 2026-09-19)
#   - RemoteIOAdapter : feed parses but has returned 0 entries in every poll
#   - DribbbleAdapter : design-jobs board; 46 jobs/poll, 0 QA matches ever — pure filter noise
#   - DeelAdapter     : Ashby board answers 200 with an EMPTY jobs array (probed 2026-09-24)
#
# Probed and NOT implemented (2026-09-24), see the audit trail in the turn notes:
#   - Virtual Vocations : /jobs/rss returns HTTP 429 + a JS cookie challenge ("Please reload") to
#                         every non-browser client — an anti-bot wall, not a feed.
#   - Remote.co         : remote.co refuses or times out on all HTTPS requests from this host
#                         (ConnectionReset then ReadTimeout) — no reachable feed.
#   - SkipTheDrive      : /feed/ and every /category/<slug>/feed/ return HTTP 200 with ZERO
#                         entries, and the site exposes no RSS <link> or category URLs at all.
#
# Every enabled source lives here. To add a new one: write an adapter class
# (see base.py) and add an instance to this list — nothing else changes.
ADAPTERS = [
    RemoteOKAdapter(),
    RemotiveAdapter(),
    HimalayasAdapter(),
    ArbeitnowAdapter(),
    JobicyAdapter(),
    WorkingNomadsAdapter(),
    WeWorkRemotelyAdapter(),
    JobspressoAdapter(),
    AuthenticJobsAdapter(),
    StartupJobsAdapter(),   # RSS, remote+engineering, 50 most recent (<=14d)
    NodeskAdapter(),        # RSS, curated remote jobs
    LandingJobsAdapter(),   # JSON API, remote-flagged only (EU-centric, slow cadence)
    RemoteFirstJobsAdapter(),  # RSS, QA-tagged feed
    RemoteComAdapter(),     # Greenhouse board - single employer, low QA volume by design
    OysterHRAdapter(),      # Ashby board - single employer, low QA volume by design
    # Company boards: one entry per verified board (see each module for probe
    # results and the boards that failed verification).
    *GREENHOUSE_BOARDS,
    *ASHBY_BOARDS,
    *LEVER_BOARDS,
    JSearchAdapter(),  # no-op unless JSEARCH_ENABLED=true — see jsearch.py
]
