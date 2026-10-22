import { create } from "zustand";
import { api } from "./api/client";
import type { AgentName, AgentState, Mission, OrbitEvent, Stats } from "./api/types";

export type Conn = "connecting" | "open" | "closed";

export type ColumnKey = "new" | "recruiter_found" | "manual_review" | "draft_created" | "sent" | "archived";

export const COLUMNS: { key: ColumnKey; title: string }[] = [
  { key: "new", title: "New" },
  { key: "recruiter_found", title: "Recruiter Found" },
  { key: "manual_review", title: "Manual Review" },
  { key: "draft_created", title: "Draft Created" },
  { key: "sent", title: "Sent" },
  { key: "archived", title: "Archived" },
];

/** Which column a mission belongs to. Archiving is a purely local/cosmetic override —
 *  it never touches the backend, which has no concept of "archived". */
export function columnFor(m: Mission, archived: boolean): ColumnKey {
  if (archived) return "archived";
  if (m.application_status === "sent") return "sent";
  if (m.application_status === "awaiting_approval" || m.application_status === "rejected" || m.application_status === "missing") return "draft_created";
  if (m.recruiter_email_status === "manual_review" || m.recruiter_email_status === "manual_lookup_needed") return "manual_review";
  if (m.recruiter_email_status === "verified") return "recruiter_found";
  return "new";
}

const ARCHIVE_KEY = "mc_archived_mission_ids";

function loadArchived(): Set<string> {
  try {
    const raw = window.localStorage.getItem(ARCHIVE_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function saveArchived(ids: Set<string>): void {
  try {
    window.localStorage.setItem(ARCHIVE_KEY, JSON.stringify([...ids]));
  } catch {
    /* localStorage unavailable (private mode, etc.) — archiving just won't persist */
  }
}

interface State {
  conn: Conn;
  apiOk: boolean;
  ownerEmail: string | null;
  agents: Record<AgentName, AgentState> | null;
  missions: Mission[];
  stats: Stats | null;
  lastUpdated: number | null;
  archivedIds: Set<string>;
  selectedMission: string | null;
  setConn: (c: Conn) => void;
  selectMission: (id: string | null) => void;
  refreshAll: () => Promise<void>;
  handleEvent: (e: OrbitEvent) => void;
  toggleArchive: (id: string) => void;
  applyMissionUpdate: (m: Mission) => void;
}

export const useStore = create<State>((set, get) => ({
  conn: "connecting",
  apiOk: false,
  ownerEmail: null,
  agents: null,
  missions: [],
  stats: null,
  lastUpdated: null,
  archivedIds: loadArchived(),
  selectedMission: null,

  setConn: (conn) => set({ conn }),
  selectMission: (selectedMission) => set({ selectedMission }),

  refreshAll: async () => {
    try {
      const [status, missions, stats] = await Promise.all([api.status(), api.missions(), api.stats()]);
      set({ agents: status.agents, missions, stats, apiOk: true, ownerEmail: status.owner_email, lastUpdated: Date.now() });
    } catch {
      set({ apiOk: false });
    }
  },

  handleEvent: (e) => {
    if (e.type === "ping" || e.type === "connected") return;
    // Any real event means the mission list/stats changed on the backend — pull the latest.
    void get().refreshAll();
  },

  toggleArchive: (id) => {
    const ids = new Set(get().archivedIds);
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    saveArchived(ids);
    set({ archivedIds: ids });
  },

  /** Merge a freshly-approved/rejected mission into the list immediately, so the card moves
   *  column right away instead of waiting for the next background poll or websocket event. */
  applyMissionUpdate: (m) => set((s) => ({ missions: s.missions.map((x) => (x.id === m.id ? m : x)) })),
}));
