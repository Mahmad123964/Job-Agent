import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Mission, MissionDetail } from "../api/types";
import { applicationLabel, clockOf, gmailLink, missionNo, platformLabel, recruiterLabel, timeAgo } from "../lib/format";
import { COLUMNS, columnFor, useStore } from "../store";
import ConfirmDialog from "./ConfirmDialog";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-white/10 py-4">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wide mb-2">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-neutral-500">{label}</div>
      <div className="text-sm text-neutral-200">{value}</div>
    </div>
  );
}

export default function DetailPanel() {
  const { selectedMission, selectMission, missions, ownerEmail, archivedIds, toggleArchive, lastUpdated, applyMissionUpdate } = useStore();
  const [detail, setDetail] = useState<MissionDetail | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedMission) { setDetail(null); return; }
    let live = true;
    api.mission(selectedMission).then((d) => live && setDetail(d)).catch(() => live && setDetail(null));
    return () => { live = false; };
    // Refetch whenever the mission list refreshes (e.g. a live event came in) while open.
  }, [selectedMission, lastUpdated]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(t);
  }, [notice]);

  const summary = missions.find((x) => x.id === selectedMission) ?? null;
  const m = detail ?? summary;
  if (!selectedMission || !m) return null;

  const archived = archivedIds.has(m.id);
  const column = COLUMNS.find((c) => c.key === columnFor(m, archived));

  const mergeUpdatedMission = (updated: Mission) => {
    applyMissionUpdate(updated);
    setDetail((d) => (d ? { ...d, ...updated } : d));
  };

  const handleApprove = async () => {
    setSending(true);
    setActionError(null);
    try {
      const result = await api.approveMission(m.id);
      mergeUpdatedMission(result.mission);
      setConfirmOpen(false);
      setNotice(`Sent to ${result.mission.recruiter_email ?? "the recruiter"}.`);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Failed to send. The draft is unchanged — you can try again.");
      setConfirmOpen(false);
    } finally {
      setSending(false);
    }
  };

  const handleReject = async () => {
    setRejecting(true);
    setActionError(null);
    try {
      const result = await api.rejectMission(m.id);
      mergeUpdatedMission(result.mission);
      setNotice("Rejected — left unsent.");
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Failed to reject. Please try again.");
    } finally {
      setRejecting(false);
    }
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-20" onClick={() => selectMission(null)} />
      <aside className="fixed right-0 top-0 bottom-0 z-30 w-full sm:w-[440px] bg-neutral-950 border-l border-white/10 overflow-y-auto">
        <div className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] text-neutral-500">Mission {missionNo(m)} · {column?.title}</div>
              <h2 className="mt-1 text-lg font-semibold text-white leading-tight">{m.title ?? "Untitled role"}</h2>
              <div className="mt-0.5 text-sm text-neutral-400">{m.company ?? "Unknown company"} · {platformLabel(m.platform)}</div>
            </div>
            <button onClick={() => selectMission(null)} className="text-neutral-500 hover:text-white text-sm shrink-0">✕</button>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <Field label="Discovered" value={timeAgo(m.discovered_at)} />
            <Field label="Discovered by" value={m.discovered_by} />
            <Field label="Recruiter" value={recruiterLabel(m)} />
            <Field label="Application" value={applicationLabel(m)} />
          </div>

          {m.notes && <p className="mt-4 text-sm text-accent bg-accent-soft rounded px-3 py-2">{m.notes}</p>}

          <div className="mt-4 flex flex-wrap gap-2">
            {m.job_url && (
              <a href={m.job_url} target="_blank" rel="noreferrer" className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-200 hover:bg-white/10">
                Open posting ↗
              </a>
            )}
            {m.draft_url && (
              <a href={gmailLink(m.draft_url, ownerEmail)} target="_blank" rel="noreferrer" className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-200 hover:bg-white/10">
                Open draft ↗
              </a>
            )}
            <button onClick={() => toggleArchive(m.id)} className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-200 hover:bg-white/10 ml-auto">
              {archived ? "Unarchive" : "Archive"}
            </button>
          </div>

          {m.application_status === "awaiting_approval" && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                onClick={() => setConfirmOpen(true)}
                disabled={sending || rejecting}
                className="text-xs font-medium px-3 py-1.5 rounded bg-accent text-white hover:bg-accent/90 disabled:opacity-50"
              >
                {sending ? "Sending…" : "Approve & Send"}
              </button>
              <button
                onClick={handleReject}
                disabled={sending || rejecting}
                className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-300 hover:bg-white/10 disabled:opacity-50"
              >
                {rejecting ? "Rejecting…" : "Reject"}
              </button>
            </div>
          )}

          {actionError && <p className="mt-3 text-sm text-red-400 bg-red-500/10 rounded px-3 py-2">{actionError}</p>}
          {notice && <p className="mt-3 text-sm text-accent bg-accent-soft rounded px-3 py-2">{notice}</p>}

          {(m.recruiter_email || m.recruiter_name) && (
            <Section title="Recruiter">
              {m.recruiter_name && <Field label="Name" value={m.recruiter_name} />}
              {m.recruiter_email && <div className="mt-2"><Field label="Email" value={m.recruiter_email} /></div>}
            </Section>
          )}

          {detail?.job_description && (
            <Section title="Job description">
              <p className="text-sm text-neutral-300 whitespace-pre-wrap">{detail.job_description}</p>
            </Section>
          )}

          {detail?.draft_content && (
            <Section title="Draft">
              {detail.draft_subject && <div className="mb-2"><Field label="Subject" value={detail.draft_subject} /></div>}
              <p className="text-sm text-neutral-300 whitespace-pre-wrap">{detail.draft_content}</p>
            </Section>
          )}

          {!!detail?.signals?.length && (
            <Section title="Signals">
              <ul className="text-sm text-neutral-300 space-y-1 list-disc list-inside">
                {detail.signals.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </Section>
          )}

          {!!detail?.timeline?.length && (
            <Section title="Timeline">
              <ul className="space-y-2">
                {detail.timeline.map((e, i) => (
                  <li key={i} className="flex items-baseline gap-2 text-xs">
                    <span className="text-neutral-500 tabular-nums shrink-0">{clockOf(e.timestamp)}</span>
                    <span className="text-neutral-300">{e.type.replace(/_/g, " ")}</span>
                    {e.agent && <span className="text-neutral-600">· {e.agent}</span>}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </aside>
      {confirmOpen && (
        <ConfirmDialog
          title="Send this application?"
          body={`Send this application to ${m.recruiter_email ?? "the recruiter"}? This cannot be undone.`}
          confirmLabel="Send"
          busy={sending}
          onConfirm={handleApprove}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </>
  );
}
