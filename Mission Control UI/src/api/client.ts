import type { ActionResult, Mission, MissionDetail, OrbitEvent, Stats, StatusResponse } from "./types";

/** Backend base URL. Set VITE_API_URL in .env / .env.local; defaults to port 8080 because
 *  port 8000 on this machine is taken by Agent 1's own backend, and port 8001 (the previous
 *  default) falls inside a Windows-reserved excluded port range. */
export const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8080").replace(/\/+$/, "");
/** WebSocket endpoint derived from the same base (http -> ws, https -> wss). */
export const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws/events";
export const API_HOST = API_BASE.replace(/^https?:\/\//, "");

/** Thrown by `post()` with the backend's own error message (FastAPI's {"detail": "..."}) when
 *  available, so the UI can show why an approve/reject was refused rather than a bare status code. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(API_BASE + path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(API_BASE + path, { method: "POST", headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `${path} -> ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — fall back to the generic message above */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => get<StatusResponse>("/api/status"),
  missions: () => get<Mission[]>("/api/missions"),
  mission: (id: string) => get<MissionDetail>(`/api/missions/${id}`),
  stats: () => get<Stats>("/api/stats"),
  events: (limit = 100) => get<OrbitEvent[]>(`/api/events?limit=${limit}`),
  /** Sends the draft — irreversible. Reuses Agent 2's exact send logic on the backend. */
  approveMission: (id: string) => post<ActionResult>(`/api/missions/${id}/approve`),
  /** Leaves the draft unsent. */
  rejectMission: (id: string) => post<ActionResult>(`/api/missions/${id}/reject`),
};

/** Reconnecting websocket with exponential backoff. Never throws into React. */
export function connectEvents(handlers: {
  onEvent: (e: OrbitEvent) => void;
  onState: (s: "connecting" | "open" | "closed") => void;
}): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: number | undefined;

  const schedule = () => {
    if (closed) return;
    const delay = Math.min(15000, 1000 * 2 ** attempt++);
    timer = window.setTimeout(open, delay);
  };
  const open = () => {
    if (closed) return;
    handlers.onState("connecting");
    try {
      ws = new WebSocket(WS_URL);
    } catch {
      schedule();
      return;
    }
    ws.onopen = () => {
      attempt = 0;
      handlers.onState("open");
    };
    ws.onmessage = (m) => {
      try {
        handlers.onEvent(JSON.parse(m.data as string) as OrbitEvent);
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onclose = () => {
      handlers.onState("closed");
      schedule();
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}
