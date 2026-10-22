// Persistent record of every job that actually went out on a channel.
// Data comes from GET /api/notifications (jobs with notified=true, newest first);
// App.jsx refreshes it on the same 60s interval as the live feed.

import { absoluteTime, relativeTime } from "../lib/time";

const CHANNEL_ICON = { email: "✉️", telegram: "✈️", discord: "💬", whatsapp: "🟢" };

function scoreClasses(score) {
  if (score >= 70) return "bg-emerald-100 text-emerald-800 ring-emerald-200";
  if (score >= 50) return "bg-amber-100 text-amber-800 ring-amber-200";
  return "bg-[#f1f3f5] text-ink-600 ring-line";
}

export default function NotificationLog({ notifications, loading, onOpen }) {
  if (loading && notifications.length === 0) {
    return <p className="text-sm text-ink-500">Loading notification log…</p>;
  }
  if (notifications.length === 0) {
    return (
      <div className="text-center py-16 text-ink-400">
        <p className="text-lg">No notifications sent yet.</p>
        <p className="text-sm">Every job that goes out on a channel is recorded here permanently.</p>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {notifications.map((job) => {
        const channels = (job.notified_channels || "").split(",").map((c) => c.trim()).filter(Boolean);
        return (
          <li
            key={job.id}
            className="bg-white border border-line rounded-[10px] p-4 transition-colors hover:bg-[#fafbfc]"
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
              <span
                className="rounded bg-emerald-50 px-2 py-0.5 text-emerald-700"
                title={absoluteTime(job.notified_at)}
              >
                sent {relativeTime(job.notified_at)}
              </span>
              {channels.length > 0 ? (
                channels.map((c) => (
                  <span key={c} className="rounded bg-[#f1f3f5] px-2 py-0.5 text-ink-600">
                    {CHANNEL_ICON[c] || "🔔"} {c}
                  </span>
                ))
              ) : (
                <span className="rounded bg-[#f1f3f5] px-2 py-0.5 text-ink-500">channel not recorded</span>
              )}
              {job.eligible_region && job.eligible_region !== "Unclear" && (
                <span className="rounded bg-[#f1f3f5] px-2 py-0.5 text-ink-600">
                  🌍 {job.eligible_region === "Specific country" && job.eligible_country ? `${job.eligible_country} only` : job.eligible_region}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
