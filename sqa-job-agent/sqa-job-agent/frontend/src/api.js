export const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
// Must equal the backend's ADMIN_API_KEY; sent on every non-GET request.
const ADMIN_API_KEY = import.meta.env.VITE_ADMIN_API_KEY || "";

// A request that never reached the backend (server down, wrong port, CORS
// block, DNS). The browser surfaces all of these as a TypeError("Failed to
// fetch") with no status code — which needs a different fix than an HTTP error.
export class NetworkError extends Error {
  constructor(path, cause) {
    super(`Backend unreachable at ${BASE_URL} (${path}): ${cause?.message || cause}`);
    this.name = "NetworkError";
    this.cause = cause;
  }
}

// The backend answered, but with an error status.
export class ApiError extends Error {
  constructor(method, path, status, detail) {
    super(`${method} ${path} failed: ${status}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    const method = (options.method || "GET").toUpperCase();
    res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(method !== "GET" && ADMIN_API_KEY ? { "X-Api-Key": ADMIN_API_KEY } : {}),
        ...(options.headers || {}),
      },
    });
  } catch (cause) {
    throw new NetworkError(path, cause);
  }
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail; } catch { /* no JSON body */ }
    throw new ApiError(options.method || "GET", path, res.status, typeof detail === "string" ? detail : "");
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  getJobs: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    return request(`/api/jobs?${qs.toString()}`);
  },
  getNotifications: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    return request(`/api/notifications?${qs.toString()}`);
  },
  getApplied: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    return request(`/api/applied?${qs.toString()}`);
  },
  // Manual application tracking — only ever called from a user click.
  updateJobStatus: (jobId, status) =>
    request(`/api/jobs/${jobId}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  // feature 1: follow-up reminders
  getDueFollowups: () => request("/api/applied/due-followups"),
  snoozeJob: (jobId, days = 3) =>
    request(`/api/jobs/${jobId}/snooze`, { method: "POST", body: JSON.stringify({ days }) }),
  // feature 2: notes
  getNotes: (jobId) => request(`/api/jobs/${jobId}/notes`),
  addNote: (jobId, text) => request(`/api/jobs/${jobId}/notes`, { method: "POST", body: JSON.stringify({ text }) }),
  // feature 4: resume
  getResume: () => request("/api/resume"),
  updateResume: (text) => request("/api/resume", { method: "PUT", body: JSON.stringify({ text }) }),
  // feature 5: analytics
  getAnalytics: () => request("/api/analytics"),
  // feature 6: company blocklist
  getBlocklist: () => request("/api/blocklist"),
  blockCompany: (companyName) =>
    request("/api/blocklist", { method: "POST", body: JSON.stringify({ company_name: companyName }) }),
  unblockCompany: (entryId) => request(`/api/blocklist/${entryId}`, { method: "DELETE" }),
  // one-off history email to a new stakeholder (email only, read-only on the server)
  sendBackfillEmail: (recipient) =>
    request("/api/admin/backfill-email", { method: "POST", body: JSON.stringify({ recipient }) }),
  // Excel export is a plain download link (the browser handles the file), not a fetch.
  exportReportUrl: () => `${BASE_URL}/api/export/report.xlsx`,
  getJob: (jobId) => request(`/api/jobs/${jobId}`),
  getArchived: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    return request(`/api/archived?${qs.toString()}`);
  },
  archiveExisting: () => request("/api/admin/archive-existing", { method: "POST" }),
  getHealth: () => request("/api/health"),
  getSources: () => request("/api/sources"),
  getPreferences: () => request("/api/preferences"),
  updatePreferences: (payload) =>
    request("/api/preferences", { method: "PUT", body: JSON.stringify(payload) }),
  pollNow: () => request("/api/poll-now", { method: "POST" }),
};
