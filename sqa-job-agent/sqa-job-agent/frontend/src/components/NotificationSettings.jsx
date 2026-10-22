import { useState, useEffect } from "react";
import { api } from "../api";

export default function NotificationSettings({ open, onClose }) {
  const [prefs, setPrefs] = useState({ keywords: [], locations: [], excluded_sources: [], score_threshold: 0, seniority: "" });
  const [keywordInput, setKeywordInput] = useState("");
  const [locationInput, setLocationInput] = useState("");
  const [saving, setSaving] = useState(false);
  // feature 4: resume text (GET/PUT /api/resume) — saved with the rest of the form
  const [resume, setResume] = useState("");
  const [resumeSaved, setResumeSaved] = useState("");
  // feature 6: company blocklist (GET/DELETE /api/blocklist)
  const [blocklist, setBlocklist] = useState([]);
  const [blockInput, setBlockInput] = useState("");

  useEffect(() => {
    if (open) {
      api.getPreferences().then((p) => {
        setPrefs(p);
        setKeywordInput(p.keywords.join(", "));
        setLocationInput(p.locations.join(", "));
      });
      api.getResume().then((r) => { setResume(r.text || ""); setResumeSaved(r.text || ""); }).catch(() => {});
      api.getBlocklist().then(setBlocklist).catch(() => {});
    }
  }, [open]);

  const unblock = async (entry) => {
    await api.unblockCompany(entry.id);
    setBlocklist((prev) => prev.filter((b) => b.id !== entry.id));
  };

  // one-off "send full history to…" (feature: onboarding a new stakeholder)
  const [backfillTo, setBackfillTo] = useState("");
  const [backfillBusy, setBackfillBusy] = useState(false);
  const [backfillResult, setBackfillResult] = useState(null); // {ok, message}

  const sendBackfill = async () => {
    const to = backfillTo.trim();
    if (!to || backfillBusy) return;
    if (!window.confirm(`Send the full notification history to ${to}?\n\nThis emails every job notified so far (in parts of 100 if long). It does not change any job data or trigger other channels.`)) return;
    setBackfillBusy(true);
    setBackfillResult(null);
    try {
      const r = await api.sendBackfillEmail(to);
      setBackfillResult({
        ok: true,
        message: r.jobs === 0 ? "Nothing to send — no jobs have been notified yet." : `Sent ${r.jobs} job${r.jobs === 1 ? "" : "s"} to ${r.recipient} in ${r.emails_sent} email${r.emails_sent === 1 ? "" : "s"}.`,
      });
      setBackfillTo("");
    } catch (e) {
      setBackfillResult({ ok: false, message: String(e.message || e) });
    } finally {
      setBackfillBusy(false);
    }
  };

  const block = async () => {
    const name = blockInput.trim();
    if (!name) return;
    const created = await api.blockCompany(name);
    setBlocklist((prev) => (prev.some((b) => b.id === created.id) ? prev : [created, ...prev]));
    setBlockInput("");
  };

  if (!open) return null;

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        keywords: keywordInput.split(",").map((s) => s.trim()).filter(Boolean),
        locations: locationInput.split(",").map((s) => s.trim()).filter(Boolean),
        score_threshold: prefs.score_threshold,
        seniority: prefs.seniority,
      };
      await api.updatePreferences(payload);
      if (resume !== resumeSaved) await api.updateResume(resume);  // triggers a background rescoring of existing jobs
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-navy/60 flex items-start sm:items-center justify-center z-50 px-4 py-6 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg space-y-4 max-h-[92vh] overflow-y-auto">
        <h2 className="font-display text-lg font-semibold tracking-tight text-ink">Notification Preferences</h2>
        <p className="text-sm text-ink-500">
          Jobs matching any of these keywords trigger a notification via your
          configured channel (Telegram / Discord / Email / WhatsApp — set in backend .env),
          as long as they score at or above the threshold below (0 = use NOTIFY_SCORE_THRESHOLD from .env).
        </p>

        <div>
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">
            Keywords (comma-separated)
          </label>
          <textarea
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            rows={3}
            className="w-full mt-1 border border-line rounded-lg px-3 py-2 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
          />
        </div>

        <div>
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Notification score threshold (0-100)</label>
          <input
            type="number"
            min="0"
            max="100"
            value={prefs.score_threshold}
            onChange={(e) => setPrefs({ ...prefs, score_threshold: Number(e.target.value) })}
            className="w-full mt-1 border border-line rounded-lg px-3 py-2 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
          />
        </div>

        <div>
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Preferred seniority</label>
          <select
            value={prefs.seniority}
            onChange={(e) => setPrefs({ ...prefs, seniority: e.target.value })}
            className="w-full mt-1 border border-line rounded-lg px-3 py-2 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
          >
            <option value="">Any level</option>
            {["junior", "mid", "senior", "lead", "unknown"].map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
        </div>

        <div>
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">
            Allowed locations (comma-separated, empty = any remote location)
          </label>
          <input
            type="text"
            value={locationInput}
            onChange={(e) => setLocationInput(e.target.value)}
            className="w-full mt-1 border border-line rounded-lg px-3 py-2 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
          />
        </div>

        <div className="border-t border-line-soft pt-4">
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">
            Paste your resume text (for "Resume match")
          </label>
          <textarea
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            rows={6}
            placeholder="Plain text is fine — skills, tools, roles. Leave empty to turn the resume-match badge off."
            className="w-full mt-1 border border-line rounded-lg px-3 py-2 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 font-mono"
          />
          <p className="mt-1 text-xs text-ink-400">
            {resume.trim().length.toLocaleString()} characters. New jobs are scored as they arrive; saving rescored the most recent jobs in the background.
          </p>
        </div>

        <div className="border-t border-line-soft pt-4">
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Blocked companies</label>
          <p className="mt-1 text-xs text-ink-400">Postings from these companies are skipped at ingestion and hidden from the feed (case-insensitive match).</p>
          <div className="mt-2 flex gap-2">
            <input
              type="text"
              value={blockInput}
              onChange={(e) => setBlockInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), block())}
              placeholder="Company name"
              className="flex-1 border border-line rounded-lg px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
            />
            <button type="button" onClick={block} disabled={!blockInput.trim()} className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-600 hover:border-ink-400 disabled:opacity-40">
              Block
            </button>
          </div>
          {blocklist.length === 0 ? (
            <p className="mt-2 text-xs text-ink-400">No companies blocked.</p>
          ) : (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {blocklist.map((b) => (
                <li key={b.id} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-[#fafbfc] px-2 py-0.5 text-xs text-ink-600">
                  {b.company_name}
                  <button type="button" onClick={() => unblock(b)} title="Unblock" className="text-ink-400 hover:text-rose-600">✕</button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t border-line-soft pt-4">
          <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Send full history to…</label>
          <p className="mt-1 text-xs text-ink-400">
            One-off email of every job notified so far, to a new recipient (e.g. a manager). Email only; nothing else changes.
          </p>
          <div className="mt-2 flex gap-2">
            <input
              type="email"
              value={backfillTo}
              onChange={(e) => setBackfillTo(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), sendBackfill())}
              placeholder="manager@example.com"
              className="flex-1 border border-line rounded-lg px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400"
            />
            <button
              type="button"
              onClick={sendBackfill}
              disabled={!backfillTo.trim() || backfillBusy}
              className="rounded-lg bg-ink px-3 py-1.5 text-sm font-medium text-white hover:bg-navy disabled:opacity-40"
            >
              {backfillBusy ? "Sending…" : "Send"}
            </button>
          </div>
          {backfillResult && (
            <p className={`mt-2 text-xs ${backfillResult.ok ? "text-emerald-700" : "text-rose-600"}`}>{backfillResult.message}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm rounded-lg text-ink-600 hover:bg-[#f1f3f5]"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
