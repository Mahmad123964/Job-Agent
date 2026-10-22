export type AgentName = "SCOUT" | "CONNECT" | "APPLY";
export type AgentStatus = "idle" | "scanning" | "processing" | "no_state";

export interface AgentState {
  agent: AgentName;
  description: string;
  status: AgentStatus;
  last_activity: string | null;
  state_file: string;
}

export interface StatusResponse {
  agents: Record<AgentName, AgentState>;
  polled_at: string;
  active_window_seconds: number;
  /** Owner's Gmail address (from the Email Agent's config.yaml), used to open Gmail
   *  deep links in the right Google account. Null if the backend couldn't read it. */
  owner_email: string | null;
}

export type Stage = "discovered" | "recruiter_found" | "application_ready" | "sent";

export interface Mission {
  id: string;
  title: string | null;
  company: string | null;
  platform: string | null;
  job_url: string | null;
  discovered_by: AgentName;
  discovered_at: string | null;
  stage: Stage;
  progress: number;
  recruiter_email_status: "pending" | "verified" | "manual_review" | "manual_lookup_needed";
  recruiter_email: string | null;
  recruiter_name: string | null;
  application_status: "none" | "awaiting_approval" | "sent" | "rejected" | "missing" | string;
  draft_id: string | null;
  draft_url: string | null;
  notes: string | null;
}

export interface MissionDetail extends Mission {
  job_description: string | null;
  draft_content: string | null;
  draft_subject: string | null;
  letter_mode: string | null;
  cv_files: string[];
  signals: string[];
  timeline: OrbitEvent[];
}

export interface Stats {
  jobs_discovered_today: number;
  jobs_discovered_total: number;
  recruiters_verified: number;
  awaiting_approval: number;
  applications_sent: number;
  manual_lookups_needed: number;
  active_automations: number;
}

export type EventType =
  | "job_found"
  | "recruiter_verified"
  | "draft_ready"
  | "sent"
  | "manual_lookup_needed"
  | "rejected"
  | "connected"
  | "ping";

export interface OrbitEvent {
  type: EventType | string;
  mission_id: string | null;
  agent: AgentName | null;
  timestamp: string;
  data: Record<string, unknown>;
  seeded?: boolean;
}

/** Response from POST /api/missions/{id}/approve or .../reject. */
export interface ActionResult {
  status: "sent" | "rejected";
  mission: Mission;
  gmail_message_id: string | null;
}
