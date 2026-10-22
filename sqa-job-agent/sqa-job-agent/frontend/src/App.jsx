import { Suspense, lazy, useEffect, useRef, useState, useCallback } from "react";
import { api, NetworkError } from "./api";
import { isPast } from "./lib/time";
import JobFeed from "./components/JobFeed";
import Filters from "./components/Filters";
import NotificationSettings from "./components/NotificationSettings";
import NotificationLog from "./components/NotificationLog";
import AppliedJobs from "./components/AppliedJobs";
import JobDetail from "./components/JobDetail";
import Analytics from "./components/Analytics";
import { LogoRadar } from "./components/LogoIcon";

// three + R3F are ~600 KB; keep them out of the main bundle.
const Globe = lazy(() => import("./components/Globe"));

const REFRESH_MS = 60000;

const TABS = [
  { id: "feed", label: "Live Feed" },
  { id: "log", label: "Notification Log" },
  { id: "applied", label: "Applied" },
  { id: "analytics", label: "Analytics" },
  { id: "archived", label: "Archived" },
];
const HEALTH_MS = 30000;

// Turn a loader failure into a message that says what to do about it.
function describeFailure(err) {
  if (err instanceof NetworkError) return "backend unreachable — check that the backend is running and reachable (port / CORS)";
  if (err?.status) return `backend returned HTTP ${err.status}${err.message.includes(" — ") ? " (" + err.message.split(" — ")[1] + ")" : ""}`;
  return String(err?.message || err);
}

function isToday(iso) {
  if (!iso) return false;
  const ts = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  const now = new Date();
  return ts.getFullYear() === now.getFullYear() && ts.getMonth() === now.getMonth() && ts.getDate() === now.getDate();
}

function Stat({ value, label, tone = "default" }) {
  const tones = { default: "text-white", accent: "text-brand-400", warn: "text-coral" };
  return (
    <div>
      <div className={`font-display text-2xl font-semibold tracking-tight ${tones[tone]}`}>{value}</div>
      <div className="mt-0.5 text-[12px] uppercase tracking-[0.04em] text-white/45">{label}</div>
    </div>
  );
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [totalJobs, setTotalJobs] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [applied, setApplied] = useState([]);
  const [appliedLoading, setAppliedLoading] = useState(true);
  const [analytics, setAnalytics] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(true);
  const [sources, setSources] = useState([]);
  const [filters, setFilters] = useState({ keyword: "", location: "", source: "", seniority: "", min_score: "", sort: "score" });
  // Live Feed sub-section: "recent" (posted within 7 days or undated) vs "older".
  // Nothing is dropped by age any more — this only decides which section a job shows in.
  const [feedAge, setFeedAge] = useState("recent");
  const [loading, setLoading] = useState(true);
  const [logLoading, setLogLoading] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tab, setTab] = useState("feed");
  const [archived, setArchived] = useState([]);
  const [archivedLoading, setArchivedLoading] = useState(true);
  // Backend connectivity from a periodic /api/health probe: null = unknown yet.
  const [backend, setBackend] = useState({ ok: null, detail: "", checkedAt: null });
  // Job open in the detail modal (null = closed). Opened from any tab; closing
  // just clears it, so the underlying tab, filters and scroll are untouched.
  const [selectedJob, setSelectedJob] = useState(null);
  const closeDetail = useCallback(() => setSelectedJob(null), []);

  // Refresh bookkeeping: when the last refresh ran and whether any loader failed.
  // Failures are shown in the UI instead of being swallowed — a 500 from the API
  // used to leave the feed silently stale while the timer kept "working".
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshError, setRefreshError] = useState(null);
  const [tick, setTick] = useState(0); // re-renders the "updated Xs ago" label

  const loadJobs = useCallback(async ({ background = false } = {}) => {
    if (!background) setLoading(true);
    try {
      const data = await api.getJobs({ ...filters, age: feedAge });
      setJobs(data);
    } finally {
      setLoading(false);
    }
  }, [filters, feedAge]);

  // The log is independent of the feed filters; it's fetched on the same
  // 60s cadence so new notifications show up without a manual reload.
  const loadNotifications = useCallback(async ({ background = false } = {}) => {
    if (!background) setLogLoading(true);
    try {
      const data = await api.getNotifications({ limit: 200 });
      setNotifications(data);
    } finally {
      setLogLoading(false);
    }
  }, []);

  const loadApplied = useCallback(async ({ background = false } = {}) => {
    if (!background) setAppliedLoading(true);
    try {
      const data = await api.getApplied({ limit: 200 });
      setApplied(data);
    } finally {
      setAppliedLoading(false);
    }
  }, []);

  const loadAnalytics = useCallback(async ({ background = false } = {}) => {
    if (!background) setAnalyticsLoading(true);
    try {
      setAnalytics(await api.getAnalytics());
    } finally {
      setAnalyticsLoading(false);
    }
  }, []);

  // Feature 1: push a follow-up reminder out by N days (user click on the Applied tab).
  const handleSnooze = useCallback(async (jobId, days = 3) => {
    try {
      const updated = await api.snoozeJob(jobId, days);
      const merge = (prev) => prev.map((j) => (j.id === jobId ? { ...j, ...updated } : j));
      setApplied(merge);
      setJobs(merge);
      setSelectedJob((prev) => (prev && prev.id === jobId ? { ...prev, ...updated } : prev));
    } catch (e) {
      console.error("Failed to snooze follow-up", e);
    }
  }, []);

  // Feature 6: block a company (confirmed in JobDetail). The feed refetch drops its jobs.
  const handleBlockCompany = useCallback(async (job) => {
    try {
      await api.blockCompany(job.company);
      setSelectedJob(null);
      loadJobs().catch(console.error);
    } catch (e) {
      console.error("Failed to block company", e);
    }
  }, [loadJobs]);

  // Manual application tracking: every call here comes from a user click on
  // a card. Patch the server, then reflect the returned job in the feed
  // immediately and refresh the Applied tab.
  const handleStatusChange = useCallback(
    async (jobId, status) => {
      try {
        const updated = await api.updateJobStatus(jobId, status);
        setJobs((prev) => prev.map((j) => (j.id === jobId ? { ...j, ...updated } : j)));
        setNotifications((prev) => prev.map((j) => (j.id === jobId ? { ...j, ...updated } : j)));
        setSelectedJob((prev) => (prev && prev.id === jobId ? { ...prev, ...updated } : prev));
        loadApplied().catch(console.error);
        loadAnalytics().catch(console.error);
      } catch (e) {
        console.error("Failed to update application status", e);
      }
    },
    [loadApplied, loadAnalytics]
  );

  const loadArchived = useCallback(async ({ background = false } = {}) => {
    if (!background) setArchivedLoading(true);
    try {
      setArchived(await api.getArchived({ limit: 500 }));
    } finally {
      setArchivedLoading(false);
    }
  }, []);

  // Hero stat = active (non-archived) job count. Comes from the health probe
  // instead of the old `/api/jobs?limit=500` fetch, which pulled ~330 KB of
  // descriptions every minute just to count rows.
  const loadTotal = useCallback(async () => {
    const h = await api.getHealth();
    setTotalJobs(h.active_jobs ?? null);
    setBackend({ ok: h.status === "ok", warming: !!h.warming_up, detail: h.db === "ok" ? "" : "database error", checkedAt: new Date() });
  }, []);

  // Refreshes EVERY tab's data (feed, log, applied, analytics, hero total).
  // Each loader is awaited independently so one failing endpoint can't stop
  // the others, and the outcome is recorded for the status indicator.
  const refreshAll = useCallback(async ({ background = false } = {}) => {
    const results = await Promise.allSettled([
      loadJobs({ background }),
      loadNotifications({ background }),
      loadApplied({ background }),
      loadAnalytics({ background }),
      loadArchived({ background }),
      loadTotal(),
    ]);
    const names = ["feed", "notification log", "applied", "analytics", "archived", "health"];
    const failed = results.map((r, i) => (r.status === "rejected" ? names[i] : null)).filter(Boolean);
    setLastRefresh(new Date());
    const firstErr = results.find((r) => r.status === "rejected")?.reason;
    if (firstErr instanceof NetworkError) setBackend({ ok: false, detail: "unreachable", checkedAt: new Date() });
    setRefreshError(failed.length ? `${failed.join(", ")} failed to refresh: ${describeFailure(firstErr)}` : null);
    if (failed.length) console.error("Auto-refresh:", results.filter((r) => r.status === "rejected").map((r) => r.reason));
  }, [loadJobs, loadNotifications, loadApplied, loadAnalytics, loadArchived, loadTotal]);

  // Keep the latest refreshAll in a ref so the interval below is created ONCE
  // and never torn down/re-armed when filters or the feed section change.
  const refreshRef = useRef(refreshAll);
  useEffect(() => { refreshRef.current = refreshAll; }, [refreshAll]);

  useEffect(() => {
    api.getSources().then(setSources).catch((e) => console.error("sources", e));
    refreshRef.current();
    const interval = setInterval(() => {
      if (document.visibilityState === "hidden") return; // browsers throttle hidden tabs; catch up on return instead
      refreshRef.current({ background: true });
    }, REFRESH_MS);
    const onVisible = () => document.visibilityState === "visible" && refreshRef.current({ background: true });
    document.addEventListener("visibilitychange", onVisible);
    const labelTimer = setInterval(() => setTick((n) => n + 1), 10000);
    // Lightweight connectivity probe, independent of the data refresh.
    const probe = async () => {
      try {
        const h = await api.getHealth();
        setBackend({ ok: h.status === "ok", warming: !!h.warming_up, detail: h.db === "ok" ? "" : "database error", checkedAt: new Date() });
      } catch (e) {
        setBackend({ ok: false, detail: e instanceof NetworkError ? "unreachable" : describeFailure(e), checkedAt: new Date() });
      }
    };
    const healthTimer = setInterval(probe, HEALTH_MS);
    const warmTimer = setInterval(() => { if (document.visibilityState !== "hidden") probe(); }, 5000);
    setTimeout(() => clearInterval(warmTimer), 180000); // fast probing only for the first 3 minutes after page load
    return () => { clearInterval(interval); clearInterval(labelTimer); clearInterval(healthTimer); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  // Re-fetch the feed (only) when its filters / section change.
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    loadJobs().catch((e) => setRefreshError(`feed failed to load: ${describeFailure(e)}`));
  }, [loadJobs]);

  const handlePollNow = async () => {
    try {
      await api.pollNow();
    } catch (e) {
      setRefreshError(`poll failed — ${e.message || e}`);
    }
    refreshRef.current();
  };

  const notifiedToday = notifications.filter((n) => isToday(n.notified_at)).length;
  const overdueFollowUps = applied.filter((j) => isPast(j.follow_up_due)).length;

  return (
    <div className="min-h-screen bg-surface">
      {/* ---------- dark header: nav + hero ---------- */}
      <header className="relative overflow-hidden bg-navy text-[#eef2f6]">
        <div
          className="pointer-events-none absolute inset-0"
          style={{ background: "radial-gradient(120% 90% at 78% 30%, rgba(34,211,238,.10), transparent 60%)" }}
        />

        <nav className="relative flex items-center gap-3.5 border-b border-white/[.08] px-6 py-4 sm:px-8">
          <div className="flex items-center gap-2.5">
            <LogoRadar size={28} className="shrink-0 text-brand-400" />
            <span className="font-display text-[17px] tracking-[-0.15px]">
              <span className="font-bold">SQA</span>
              <span className="font-medium text-[#eef2f6]/80"> Job Agent</span>
            </span>
          </div>
          <div className="flex-1" />
          <span
            data-testid="backend-status"
            title={backend.ok === null ? "Checking backend…" : backend.ok ? (backend.warming ? "Backend up — loading models and running the first poll; data appears when that finishes" : `Backend connected (checked ${backend.checkedAt?.toLocaleTimeString()})`) : `Backend ${backend.detail || "disconnected"} — is the backend running on ${api.exportReportUrl().replace("/api/export/report.xlsx", "")}?`}
            className={`mr-2 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.06em] ${
              backend.ok === null ? "border-white/15 text-white/50" : backend.ok ? (backend.warming ? "border-amber-400/40 text-amber-300" : "border-emerald-400/30 text-emerald-300") : "border-rose-400/40 text-rose-300"
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${backend.ok === null ? "bg-white/40" : backend.ok ? (backend.warming ? "bg-amber-400 animate-live" : "bg-emerald-400 animate-live") : "bg-rose-400"}`} />
            Backend: {backend.ok === null ? "checking" : backend.ok ? (backend.warming ? "connected · starting up" : "connected") : "disconnected"}
          </span>
          <div className="flex items-center gap-1 sm:gap-2">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`rounded-md px-3 py-1.5 text-[13.5px] transition-colors ${
                  tab === t.id ? "font-medium text-[#eef2f6]" : "text-[#eef2f6]/60 hover:text-[#eef2f6]"
                }`}
              >
                {t.label}
                {((t.id === "log" && notifications.length > 0) || (t.id === "applied" && applied.length > 0) || (t.id === "archived" && archived.length > 0)) && (
                  <span className="ml-1.5 rounded-full bg-white/10 px-1.5 py-0.5 font-mono text-[10.5px] text-[#eef2f6]/80">
                    {t.id === "log" ? notifications.length : t.id === "applied" ? applied.length : archived.length}
                  </span>
                )}
                {t.id === "applied" && overdueFollowUps > 0 && (
                  <span title={`${overdueFollowUps} follow-up(s) due`} className="ml-1 rounded-full bg-amber-400/20 px-1.5 py-0.5 font-mono text-[10.5px] text-amber-300">
                    ⏰ {overdueFollowUps}
                  </span>
                )}
              </button>
            ))}
            <a
              href={api.exportReportUrl()}
              download
              title="Download an Excel report: summary, poll history, and every saved job"
              className="ml-2 rounded-md border border-white/15 px-3 py-1.5 text-[13.5px] text-[#eef2f6]/80 transition-colors hover:border-brand-400/50 hover:text-[#eef2f6]"
            >
              Export Report
            </a>
            <button
              onClick={() => setSettingsOpen(true)}
              className="rounded-md border border-white/15 px-3 py-1.5 text-[13.5px] text-[#eef2f6]/80 transition-colors hover:border-brand-400/50 hover:text-[#eef2f6]"
            >
              Notification Settings
            </button>
          </div>
        </nav>

        <div className="relative grid items-center gap-7 px-6 pb-10 pt-11 sm:px-8 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="max-w-[620px]">
            <div className="inline-flex items-center gap-2 rounded-full border border-brand-400/30 py-[5px] pl-2 pr-[11px] font-mono text-[11px] uppercase tracking-[0.08em] text-brand-400">
              <span className="h-1.5 w-1.5 animate-live rounded-full bg-brand-400" />
              Monitoring {sources.length} sources
            </div>
            <h1 className="mb-2.5 mt-4 font-display text-[34px] font-bold leading-[1.08] tracking-[-1.1px] sm:text-[40px]" style={{ textWrap: "pretty" }}>
              Every QA job, watched end to end.
            </h1>
            <p className="max-w-[48ch] text-[15.5px] leading-[1.55] text-[#eef2f6]/65" style={{ textWrap: "pretty" }}>
              Remote SQA roles pulled from every board on a schedule — normalised, de-duplicated, scored, and pushed to you the moment they land, not the morning after.
            </p>
            <div className="mt-7 flex flex-wrap gap-8">
              <Stat value={totalJobs ?? "—"} label="Active jobs" />
              <Stat value={sources.length} label="Sources" tone="accent" />
              <Stat value={notifiedToday} label="Notified today" tone={notifiedToday > 0 ? "warn" : "default"} />
            </div>
          </div>

          <div className="hidden justify-self-end md:block">
            <Suspense fallback={<div style={{ width: 280, height: 280 }} aria-hidden="true" />}>
              <Globe size={280} />
            </Suspense>
          </div>
        </div>
      </header>

      {/* ---------- light body ---------- */}
      <main className="mx-auto max-w-4xl space-y-5 px-6 py-7 sm:px-8">
        <div className="flex items-center justify-between gap-3 text-xs" data-testid="refresh-status">
          <span className={refreshError ? "text-rose-600" : "text-ink-400"}>
            {refreshError
              ? `⚠ ${refreshError}`
              : lastRefresh
                ? `Auto-refresh every ${REFRESH_MS / 1000}s · updated ${Math.max(0, Math.round((Date.now() - lastRefresh.getTime()) / 1000))}s ago`
                : "Loading…"}
            <span hidden>{tick}</span>
          </span>
          <button onClick={() => refreshRef.current()} className="text-ink-400 hover:text-ink">Refresh now</button>
        </div>

        {tab === "feed" ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {[
                ["recent", "Recent (last 7 days)"],
                ["older", "Older postings"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setFeedAge(id)}
                  className={`rounded-lg px-3 py-1.5 text-[13.5px] transition-colors ${
                    feedAge === id ? "bg-ink font-medium text-white" : "border border-line bg-white text-ink-600 hover:border-ink-400"
                  }`}
                >
                  {label}
                </button>
              ))}
              <span className="ml-1 text-xs text-ink-400">
                {loading ? "…" : `${jobs.length} job${jobs.length === 1 ? "" : "s"}`}
                {feedAge === "recent" ? " · posted within 7 days, or undated" : " · posted more than 7 days ago"}
              </span>
            </div>
            <Filters
              filters={filters}
              setFilters={setFilters}
              sources={sources}
              onRefresh={loadJobs}
              onPollNow={handlePollNow}
            />
            <JobFeed jobs={jobs} loading={loading} onStatusChange={handleStatusChange} onOpen={setSelectedJob} />
          </>
        ) : tab === "applied" ? (
          <>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-ink-500">
                Jobs you marked as applied, most recent first. You apply on the source site yourself — this is only the record.
              </p>
              <button
                onClick={loadApplied}
                className="rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink-600 hover:border-ink-400"
              >
                Refresh
              </button>
            </div>
            <AppliedJobs jobs={applied} loading={appliedLoading} onStatusChange={handleStatusChange} onOpen={setSelectedJob} onSnooze={handleSnooze} />
          </>
        ) : tab === "archived" ? (
          <>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-ink-500">
                Historical jobs moved out of the active views (read-only). Not counted in the hero stat.
              </p>
              <button onClick={() => loadArchived()} className="rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink-600 hover:border-ink-400">
                Refresh
              </button>
            </div>
            <JobFeed jobs={archived} loading={archivedLoading} onOpen={setSelectedJob} />
          </>
        ) : tab === "analytics" ? (
          <>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-ink-500">Trends aggregated from the jobs table. Refreshes with the feed every 60s.</p>
              <button onClick={loadAnalytics} className="rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink-600 hover:border-ink-400">
                Refresh
              </button>
            </div>
            <Analytics data={analytics} loading={analyticsLoading} />
          </>
        ) : (
          <>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-ink-500">
                Every job that was actually delivered to a channel, newest first. Persisted in the database.
              </p>
              <button
                onClick={loadNotifications}
                className="rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink-600 hover:border-ink-400"
              >
                Refresh
              </button>
            </div>
            <NotificationLog notifications={notifications} loading={logLoading} onOpen={setSelectedJob} />
          </>
        )}

      </main>

      <NotificationSettings open={settingsOpen} onClose={() => { setSettingsOpen(false); refreshRef.current(); }} />
      <JobDetail job={selectedJob} onClose={closeDetail} onStatusChange={handleStatusChange} onBlockCompany={handleBlockCompany} />
    </div>
  );
}
