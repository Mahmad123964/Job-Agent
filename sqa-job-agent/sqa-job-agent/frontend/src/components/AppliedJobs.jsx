// Permanent record of jobs the user marked as applied (GET /api/applied,
// newest first). Every entry got here through an explicit click — the app
// never submits applications itself. Refreshed by App.jsx on the same 60s
// cadence as the feed and the notification log.
import { absoluteTime, isPast, relativeTime } from "../lib/time";

function scoreClasses(score) {
  if (score >= 70) return "bg-emerald-100 text-emerald-800 ring-emerald-200";
  if (score >= 50) return "bg-amber-100 text-amber-800 ring-amber-200";
  return "bg-[#f1f3f5] text-ink-600 ring-line";
}

export default function AppliedJobs({ jobs, loading, onStatusChange, onOpen, onSnooze }) {
  if (loading && jobs.length === 0) {
    return <p className="text-sm text-ink-500">Loading applied jobs…</p>;
  }
  if (jobs.length === 0) {
    return (
      <div className="text-center py-16 text-ink-400">
        <p className="text-lg">Nothing marked as applied yet.</p>
        <p className="text-sm">Use “Apply ↗” on a card in the Live Feed, then “Mark as Applied” once you've submitted on the source site.</p>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {jobs.map((job) => (
        <li
          key={job.id}
          className={`bg-white border rounded-[10px] p-4 transition-colors hover:bg-[#fafbfc] ${
            job.follow_up_due && isPast(job.follow_up_due) ? "border-amber-200 border-l-4 border-l-amber-400" : "border-line"
          }`}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <button
                type="button"
                onClick={() => onOpen && onOpen(job)}
                title="View details"
                className="text-left font-medium text-[14.5px] text-ink hover:text-brand-600 hover:underline"
              >
                {job.title}
              </button>
              <p className="text-[13.5px] text-ink-500">
                {job.company || "Unknown company"} · {job.location}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1 shrink-0">
              <span className={`inline-flex items-baseline gap-0.5 rounded-full px-2.5 py-1 ring-1 ${scoreClasses(job.score ?? 0)}`}>
                <span className="text-sm font-bold leading-none">{job.score ?? 0}</span>
                <span className="text-[10px] font-medium opacity-70">/100</span>
              </span>
              <span className="font-mono text-[11px] text-ink-400">{job.source}</span>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="rounded bg-emerald-50 px-2 py-0.5 text-emerald-700" title={absoluteTime(job.applied_at)}>
              Applied ✓ {relativeTime(job.applied_at)}
            </span>
            {job.follow_up_due && (
              isPast(job.follow_up_due) ? (
                <span className="rounded bg-amber-100 px-2 py-0.5 font-medium text-amber-800" title={`Follow-up was due ${absoluteTime(job.follow_up_due)}`}>
                  ⏰ Follow up — due {relativeTime(job.follow_up_due)}
                </span>
              ) : (
                <span className="rounded bg-[#f1f3f5] px-2 py-0.5 text-ink-500" title={absoluteTime(job.follow_up_due)}>
                  follow up {relativeTime(job.follow_up_due).replace(" ago", "")}
                </span>
              )
            )}
            {job.eligible_region && job.eligible_region !== "Unclear" && (
              <span className="rounded bg-[#f1f3f5] px-2 py-0.5 text-ink-600">
                🌍 {job.eligible_region === "Specific country" && job.eligible_country ? `${job.eligible_country} only` : job.eligible_region}
              </span>
            )}
            <span className="flex-1" />
            {onSnooze && job.follow_up_due && (
              <button
                type="button"
                onClick={() => onSnooze(job.id, 3)}
                title="Push the follow-up reminder 3 days out"
                className="rounded-md border border-line px-2 py-0.5 text-ink-600 hover:border-amber-300 hover:text-amber-700"
              >
                Snooze 3 days
              </button>
            )}
            <button
              type="button"
              onClick={() => onOpen && onOpen(job)}
              className="rounded-md border border-line px-2 py-0.5 text-ink-600 hover:border-ink-400"
            >
              Details
            </button>
            {onStatusChange && (
              <button
                onClick={() => onStatusChange(job.id, "not_applied")}
                title="Move back to the queue (clears the applied date)"
                className="rounded-md px-2 py-0.5 text-ink-400 hover:text-ink-600"
              >
                Undo
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
