import { useMemo } from "react";
import { COLUMNS, columnFor, useStore } from "../store";
import MissionCard from "./MissionCard";

export default function KanbanBoard() {
  const { missions, archivedIds, selectMission } = useStore();

  const byColumn = useMemo(() => {
    const map = Object.fromEntries(COLUMNS.map((c) => [c.key, [] as typeof missions]));
    for (const m of missions) map[columnFor(m, archivedIds.has(m.id))].push(m);
    for (const list of Object.values(map)) list.sort((a, b) => (b.discovered_at ?? "").localeCompare(a.discovered_at ?? ""));
    return map;
  }, [missions, archivedIds]);

  if (missions.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-neutral-500">
        No jobs tracked yet — this fills in as SCOUT, CONNECT and APPLY run.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-x-auto overflow-y-hidden">
      <div className="flex h-full gap-4 p-4 min-w-max">
        {COLUMNS.map((col) => {
          const items = byColumn[col.key];
          return (
            <div key={col.key} className="flex flex-col w-72 shrink-0 h-full">
              <div className="flex items-center justify-between px-1 pb-2 shrink-0">
                <h2 className="text-xs font-semibold text-neutral-300 uppercase tracking-wide">{col.title}</h2>
                <span className="text-xs text-neutral-500 tabular-nums">{items.length}</span>
              </div>
              <div className="flex flex-col gap-2 overflow-y-auto pr-1 pb-2 min-h-0 flex-1">
                {items.length === 0 && <div className="text-xs text-neutral-600 px-1 py-2">Empty</div>}
                {items.map((m) => (
                  <MissionCard key={m.id} mission={m} column={col.key} onOpen={() => selectMission(m.id)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
