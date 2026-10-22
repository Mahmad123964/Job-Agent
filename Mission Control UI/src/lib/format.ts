import type { Mission } from "../api/types";

export const missionNo = (m: Mission) => "#" + m.id.slice(2, 6).toUpperCase();

export const timeAgo = (iso: string | null) => {
  if (!iso) return "—";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return `${Math.max(0, Math.round(d))}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
};

export const clockOf = (iso: string) => iso.slice(11, 19) || iso;

export const recruiterLabel = (m: Mission) =>
  ({ verified: "VERIFIED", pending: "PENDING", manual_review: "MANUAL REVIEW", manual_lookup_needed: "MANUAL LOOKUP" })[m.recruiter_email_status] ?? m.recruiter_email_status.toUpperCase();

export const applicationLabel = (m: Mission) =>
  ({ none: "—", awaiting_approval: "AWAITING APPROVAL", sent: "SENT", rejected: "REJECTED", missing: "DRAFT MISSING" })[m.application_status] ?? m.application_status.toUpperCase();

export const platformLabel = (p: string | null) => (p ?? "unknown").replace("weworkremotely", "WWR").toUpperCase();

/**
 * Appends ?authuser=<ownerEmail> to a mail.google.com deep link so it opens in the owner's
 * account instead of whichever Google account the browser defaults to. Non-Gmail URLs (e.g.
 * a job posting link) and links opened while the owner email hasn't loaded yet pass through
 * unchanged rather than guessing an account.
 */
export const gmailLink = (url: string, ownerEmail: string | null | undefined): string => {
  if (!ownerEmail || !/^https?:\/\/mail\.google\.com\//i.test(url)) return url;
  try {
    const u = new URL(url);
    u.searchParams.set("authuser", ownerEmail);
    return u.toString();
  } catch {
    return url;
  }
};
