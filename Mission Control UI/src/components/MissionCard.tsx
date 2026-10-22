import type { Mission } from "../api/types";
import { platformLabel, timeAgo } from "../lib/format";
import type { ColumnKey } from "../store";

/** A short badge for states that need attention, using the single accent color. */
function attentionBadge(m: Mission, column: ColumnKey): string | null {
  if (column === "manual_review") return m.recruiter_email_status === "manual_lookup_needed" ? "Needs manual lookup" : "Needs manual review";
  if (column === "draft_created" && m.application_status === "rejected") return "Rejected";
  if (column === "draft_created" && m.application_status === "missing") return "Draft missing";
  return null;
}

export default function MissionCard({ mission, column, onOpen }: { mission: Mission; column: ColumnKey; onOpen: () => void }) {
  const badge = attentionBadge(mission, column);
  return (
    <button
      onClick={onOpen}
      className="w-full text-left card card-hover p-3 flex flex-col gap-2"
    >
      <div className="text-sm font-medium text-white leading-snug line-clamp-2">{mission.title ?? "Untitled role"}</div>
      <div className="text-xs text-neutral-400 truncate">{mission.company ?? "Unknown company"}</div>
      <div className="flex items-center justify-between gap-2 pt-1">
        <span className="text-[11px] px-1.5 py-0.5 rounded bg-white/5 text-neutral-400">{platformLabel(mission.platform)}</span>
        <span className="text-[11px] text-neutral-500 whitespace-nowrap">{timeAgo(mission.discovered_at)}</span>
      </div>
      {badge && <span className="text-[11px] px-1.5 py-0.5 rounded bg-accent-soft text-accent w-fit">{badge}</span>}
    </button>
  );
}
