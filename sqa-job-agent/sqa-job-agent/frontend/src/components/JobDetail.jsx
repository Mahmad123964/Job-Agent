// In-app detail view for one job. Opened by clicking a job title anywhere
// (Live Feed / Notification Log / Applied). This modal is the only place a
// title-click leads to; the external posting is reached via the explicit
// "Apply on <source> ↗" button below — never as a default click.
import { useEffect, useState } from "react";
import { api } from "../api";
import { absoluteTime, relativeTime } from "../lib/time";

function scoreClasses(score) {
  if (score >= 70) return "bg-emerald-100 text-emerald-800 ring-emerald-200";
  if (score >= 50) return "bg-amber-100 text-amber-800 ring-amber-200";
  return "bg-[#f1f3f5] text-ink-600 ring-line";
}

function eligibilityLabel(job) {
  const region = job.eligible_region || "Unclear";
  if (region === "Specific country" && job.eligible_country) return `${job.eligible_country} only`;
  return region;
}

function formatSalary(job) {
  if (!job.salary_min && !job.salary_max) return null;
  const fmt = (n) => (n ? Math.round(n).toLocaleString() : "?");
  return `${job.salary_currency || ""} ${fmt(job.salary_min)} – ${fmt(job.salary_max)}`.trim();
}

function Field({ label, children }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">{label}</div>
      <div className="mt-0.5 text-[13.5px] text-ink">{children}</div>
    </div>
  );
}

function Chip({ children, tone = "slate" }) {
  const tones = {
    slate: "bg-[#f1f3f5] text-ink-600",
    green: "bg-emerald-50 text-emerald-700",
    blue: "bg-brand-50 text-brand-700",
    rose: "bg-rose-50 text-rose-700",
    amber: "bg-amber-50 text-amber-700",
  };
  return <span className={`rounded px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>;
}

// Feature 2: free-text journal per job (GET/POST /api/jobs/{id}/notes).
function NotesSection({ jobId }) {
  const [notes, setNotes] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setNotes([]);
    setError(null);
    api.getNotes(jobId).then((n) => !cancelled && setNotes(n)).catch((e) => !cancelled && setError(String(e.message || e)));
    return () => { cancelled = true; };
  }, [jobId]);

  const submit = async (e) => {
    e.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    try {
      const created = await api.addNote(jobId, value);
      setNotes((prev) => [created, ...prev]);
      setText("");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">Notes</div>
      <form onSubmit={submit} className="mt-1.5 flex gap-2">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "interview scheduled Tue", "rejected"'
          className="flex-1 rounded-lg border border-line px-3 py-1.5 text-[13.5px] text-ink focus:border-ink-400 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!text.trim() || busy}
          className="rounded-lg bg-ink px-3 py-1.5 text-sm font-medium text-white hover:bg-navy disabled:opacity-40"
        >
          Add note
        </button>
      </form>
      {error && <p className="mt-1.5 text-xs text-rose-600">{error}</p>}
      {notes.length === 0 ? (
        <p className="mt-2 text-xs text-ink-400">No notes yet.</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {notes.map((n) => (
            <li key={n.id} className="rounded-lg border border-line-soft bg-[#fafbfc] px-3 py-2">
              <div className="whitespace-pre-line text-[13.5px] text-ink">{n.text}</div>
              <div className="mt-0.5 font-mono text-[11px] text-ink-400" title={absoluteTime(n.created_at)}>{relativeTime(n.created_at)}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function JobDetail({ job: listJob, onClose, onStatusChange, onBlockCompany }) {
  // List endpoints omit the (large) description; fetch the full record when
  // the modal opens and overlay it on the list row. Status changes from the
  // parent keep flowing through `listJob`.
  const [full, setFull] = useState(null);
  const [fullError, setFullError] = useState(null);
  useEffect(() => {
    if (!listJob) { setFull(null); setFullError(null); return undefined; }
    let cancelled = false;
    setFullError(null);
    api.getJob(listJob.id).then((j) => !cancelled && setFull(j)).catch((e) => !cancelled && setFullError(String(e.message || e)));
    return () => { cancelled = true; };
  }, [listJob?.id]);
  const job = listJob ? { ...(full && full.id === listJob.id ? full : {}), ...listJob, description: full && full.id === listJob.id ? full.description : undefined } : null;

  // Lock page scroll while open (restores the exact scroll position on close)
  // and close on Escape.
  useEffect(() => {
    if (!job) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [job, onClose]);

  if (!job) return null;

  const status = job.application_status || "not_applied";
  const salary = formatSalary(job);
  const skills = job.required_skills || [];
  const tags = (job.tags || "").split(",").map((t) => t.trim()).filter(Boolean);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-navy/60 px-4 py-6 sm:items-center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="job-detail-title"
    >
      <div className="w-full max-w-2xl rounded-xl bg-white shadow-xl">
        {/* header */}
        <div className="flex items-start justify-between gap-4 border-b border-line-soft px-6 py-5">
          <div className="min-w-0">
            <h2 id="job-detail-title" className="font-display text-xl font-semibold leading-tight tracking-tight text-ink">
              {job.title}
            </h2>
            <p className="mt-1 text-[13.5px] text-ink-500">
              {job.company || "Unknown company"} · {job.location}
              {salary && <span className="font-mono text-ink-400"> · {salary}</span>}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {status === "applied" && <Chip tone="green">Applied ✓ {relativeTime(job.applied_at)}</Chip>}
              {status === "applying" && <Chip tone="amber">Applying…</Chip>}
              <Chip tone="blue">{job.seniority || "unknown"}</Chip>
              {job.is_remote && <Chip tone="green">remote</Chip>}
              {job.is_worldwide && <Chip tone="green">worldwide</Chip>}
              <Chip tone={job.pakistan_eligible ? "green" : "rose"}>{job.pakistan_eligible ? "Pakistan-eligible" : "location-restricted"}</Chip>
              <Chip tone={job.eligible_region === "Unclear" || !job.eligible_region ? "slate" : "amber"}>🌍 {eligibilityLabel(job)}</Chip>
              {!job.is_sqa_relevant && <Chip tone="rose">low SQA relevance</Chip>}
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <span className={`inline-flex items-baseline gap-0.5 rounded-full px-2.5 py-1 ring-1 ${scoreClasses(job.score ?? 0)}`}>
              <span className="text-sm font-bold leading-none">{job.score ?? 0}</span>
              <span className="text-[10px] font-medium opacity-70">/100</span>
            </span>
            {job.resume_match_score !== null && job.resume_match_score !== undefined && (
              <span
                title="Cosine similarity between your resume and this posting"
                className={`rounded px-2 py-0.5 text-xs ${
                  job.resume_match_score >= 70 ? "bg-emerald-50 text-emerald-700" : job.resume_match_score >= 40 ? "bg-amber-50 text-amber-700" : "bg-[#f1f3f5] text-ink-600"
                }`}
              >
                Resume match: {job.resume_match_score}%
              </span>
            )}
            <button onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 text-sm text-ink-400 hover:bg-[#f1f3f5] hover:text-ink">
              ✕
            </button>
          </div>
        </div>

        {/* body */}
        <div className="max-h-[60vh] space-y-5 overflow-y-auto px-6 py-5">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
            <Field label="Source"><span className="font-mono">{job.source}</span></Field>
            <Field label="Matched keyword">"{job.matched_keyword}"</Field>
            <Field label="Seniority">{job.seniority || "unknown"}</Field>
            <Field label="Eligible region">{eligibilityLabel(job)}</Field>
            <Field label="Posted">
              {job.posted_at ? <span title={absoluteTime(job.posted_at)}>{relativeTime(job.posted_at)}</span> : <span className="text-ink-400">unknown</span>}
            </Field>
            <Field label="Discovered">
              <span title={absoluteTime(job.discovered_at)}>{relativeTime(job.discovered_at)}</span>
            </Field>
            {salary && <Field label="Salary"><span className="font-mono">{salary}</span></Field>}
            {job.follow_up_due && (
              <Field label="Follow-up due">
                <span title={absoluteTime(job.follow_up_due)}>{relativeTime(job.follow_up_due).replace(" ago", " ago (overdue)")}</span>
              </Field>
            )}
            {job.notified_at && (
              <Field label="Notified">
                <span title={absoluteTime(job.notified_at)}>{relativeTime(job.notified_at)}</span>
                {job.notified_channels && <span className="text-ink-400"> via {job.notified_channels}</span>}
              </Field>
            )}
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">Score breakdown</div>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {Object.entries(job.score_breakdown || {}).map(([name, value]) => (
                <span key={name} className="rounded bg-[#f1f3f5] px-2 py-1 font-mono text-[11px] text-ink-600">
                  {name.replaceAll("_", " ")}: {value}
                </span>
              ))}
            </div>
          </div>

          {skills.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">Required skills</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {skills.map((s) => (
                  <span key={s} className="rounded-md border border-line bg-[#fafbfc] px-2 py-0.5 font-mono text-[11.5px] text-ink-600">{s}</span>
                ))}
              </div>
            </div>
          )}

          {tags.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">Source tags</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {tags.map((t) => <Chip key={t}>{t}</Chip>)}
              </div>
            </div>
          )}

          <div>
            <div className="text-[11px] uppercase tracking-[0.06em] text-ink-400">Description</div>
            {job.description === undefined ? (
              <p className="mt-1.5 text-[13.5px] text-ink-400">{fullError ? `Couldn't load the description: ${fullError}` : "Loading description…"}</p>
            ) : job.description ? (
              <p className="mt-1.5 whitespace-pre-line text-[13.5px] leading-relaxed text-ink-600">{job.description}</p>
            ) : (
              <p className="mt-1.5 text-[13.5px] text-ink-400">This source doesn't provide a description — see the full posting on {job.source}.</p>
            )}
          </div>

          <NotesSection jobId={job.id} />
        </div>

        {/* footer: the only external navigation, plus the manual tracking actions */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-soft px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="text-xs text-ink-400">
              You apply on the source site yourself; the buttons only record your progress here.
            </span>
            {onBlockCompany && job.company && (
              <button
                type="button"
                onClick={() => {
                  if (window.confirm(`Block "${job.company}"?\n\nFuture postings from this company will be skipped at ingestion and its existing jobs hidden from the feed. You can unblock it in Notification Settings.`)) {
                    onBlockCompany(job);
                  }
                }}
                className="whitespace-nowrap rounded-md px-2 py-1 text-xs text-ink-400 hover:bg-rose-50 hover:text-rose-700"
                title="Skip this company's postings from now on"
              >
                Block company
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {status !== "applied" ? (
              <>
                <a
                  href={job.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => onStatusChange && onStatusChange(job.id, "applying")}
                  className="rounded-lg bg-ink px-3.5 py-1.5 text-sm font-medium text-white hover:bg-navy"
                >
                  Apply on {job.source} ↗
                </a>
                {onStatusChange && (
                  <button
                    onClick={() => onStatusChange(job.id, "applied")}
                    className="rounded-lg border border-line px-3.5 py-1.5 text-sm font-medium text-ink-600 hover:border-emerald-300 hover:text-emerald-700"
                  >
                    Mark as Applied
                  </button>
                )}
              </>
            ) : (
              <>
                <a
                  href={job.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg border border-line px-3.5 py-1.5 text-sm font-medium text-ink-600 hover:border-ink-400"
                >
                  View posting on {job.source} ↗
                </a>
                {onStatusChange && (
                  <button
                    onClick={() => onStatusChange(job.id, "not_applied")}
                    title="Move back to the queue (clears the applied date)"
                    className="rounded-lg px-3 py-1.5 text-sm text-ink-400 hover:text-ink-600"
                  >
                    Undo
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
