import { useEffect, useState } from "react";
import { useStore } from "../store";

function useNow(intervalMs = 1000) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
}

function relative(ts: number | null): string {
  if (!ts) return "never";
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-lg font-semibold text-white tabular-nums">{value}</span>
      <span className="text-xs text-neutral-500">{label}</span>
    </div>
  );
}

export default function TopBar() {
  const { conn, apiOk, stats, lastUpdated, refreshAll } = useStore();
  const [refreshing, setRefreshing] = useState(false);
  useNow(); // re-render every second so "X ago" stays fresh

  const live = conn === "open" && apiOk;
  const statusLabel = live ? "Live" : conn === "connecting" ? "Connecting…" : "Offline";
  const dotClass = live ? "bg-accent" : conn === "connecting" ? "bg-neutral-500 animate-pulse" : "bg-red-500";

  const onRefresh = async () => {
    setRefreshing(true);
    await refreshAll();
    setRefreshing(false);
  };

  return (
    <header className="flex items-center justify-between gap-6 px-6 h-16 border-b border-white/10 shrink-0">
      <div className="flex items-center gap-3 min-w-0">
        <h1 className="text-sm font-semibold text-white whitespace-nowrap">Job Application Pipeline</h1>
      </div>

      <div className="flex items-center gap-8 overflow-x-auto">
        <Stat label="discovered" value={stats?.jobs_discovered_total ?? "—"} />
        <Stat label="recruiters verified" value={stats?.recruiters_verified ?? "—"} />
        <Stat label="sent" value={stats?.applications_sent ?? "—"} />
        {!!stats?.manual_lookups_needed && <Stat label="need review" value={stats.manual_lookups_needed} />}
      </div>

      <div className="flex items-center gap-4 shrink-0">
        <div className="flex items-center gap-2 text-xs text-neutral-400" title={live ? "Connected to the backend" : "Not receiving live updates"}>
          <span className={`h-2 w-2 rounded-full ${dotClass}`} />
          <span>{statusLabel}</span>
          <span className="text-neutral-600">· updated {relative(lastUpdated)}</span>
        </div>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-200 hover:bg-white/10 disabled:opacity-50 transition-colors"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
    </header>
  );
}
