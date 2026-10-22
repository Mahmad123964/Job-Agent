function scoreClasses(score) {
  if (score >= 70) return "bg-emerald-100 text-emerald-800 ring-emerald-200";
  if (score >= 50) return "bg-amber-100 text-amber-800 ring-amber-200";
  return "bg-[#f1f3f5] text-ink-600 ring-line";
}

function ScoreBadge({ score }) {
  return (
    <span
      title="Weighted score: SQA relevance, remote, junior-friendly, Pakistan-eligible, freshness"
      className={`shrink-0 inline-flex items-baseline gap-0.5 rounded-full px-2.5 py-1 ring-1 ${scoreClasses(score)}`}
    >
      <span className="text-sm font-bold leading-none">{score}</span>
      <span className="text-[10px] font-medium opacity-70">/100</span>
    </span>
  );
}

function Chip({ children, tone = "slate" }) {
  const tones = {
    slate: "bg-[#f1f3f5] text-ink-600",
    green: "bg-emerald-50 text-emerald-700",
    blue: "bg-brand-50 text-brand-700",
    rose: "bg-rose-50 text-rose-700",
    amber: "bg-amber-50 text-amber-700",
  };
  return <span className={`rounded px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>;
}

function eligibilityLabel(job) {
  const region = job.eligible_region || "Unclear";
  if (region === "Specific country" && job.eligible_country) return `${job.eligible_country} only`;
  return region;
}

function eligibilityTone(job) {
  const region = job.eligible_region || "Unclear";
  if (region === "Worldwide" || region === "Asia-Pacific") return "green";
  if (region === "Unclear") return "slate";
  return "amber";
}

function formatSalary(job) {
  if (!job.salary_min && !job.salary_max) return null;
  const fmt = (n) => (n ? Math.round(n).toLocaleString() : "?");
  return `${job.salary_currency || ""} ${fmt(job.salary_min)} – ${fmt(job.salary_max)}`.trim();
}

// Only rendered when a resume is set (score is null otherwise — never shown as 0%).
function ResumeBadge({ score }) {
  if (score === null || score === undefined) return null;
  const tone = score >= 70 ? "text-emerald-700 bg-emerald-50" : score >= 40 ? "text-amber-700 bg-amber-50" : "text-ink-600 bg-[#f1f3f5]";
  return (
    <span title="Cosine similarity between your resume and this posting" className={`rounded px-2 py-0.5 text-xs ${tone}`}>
      Resume match: {score}%
    </span>
  );
}

function ApplicationChip({ status }) {
  if (status === "applied") return <Chip tone="green">Applied ✓</Chip>;
  if (status === "applying") return <Chip tone="amber">Applying…</Chip>;
  return null;
}

// Manual application tracking. "Apply ↗" opens the source posting in a new
// tab (the user applies there themselves) and records "applying"; "Mark as
// Applied" records "applied". Nothing is submitted by the app.
function ApplicationActions({ job, onStatusChange }) {
  const status = job.application_status || "not_applied";
  if (status === "applied" || !onStatusChange) return null;
  return (
    <div className="flex items-center gap-1.5">
      <a
        href={job.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => onStatusChange(job.id, "applying")}
        className="rounded-md bg-ink px-2.5 py-1 text-xs font-medium text-white hover:bg-navy"
      >
        Apply ↗
      </a>
      <button
        onClick={() => onStatusChange(job.id, "applied")}
        className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink-600 hover:border-emerald-300 hover:text-emerald-700"
      >
        Mark as Applied
      </button>
    </div>
  );
}

export default function JobFeed({ jobs, loading, onStatusChange, onOpen }) {
  if (loading) {
    return <p className="text-sm text-ink-500">Loading jobs…</p>;
  }
  if (jobs.length === 0) {
    return (
      <div className="text-center py-16 text-ink-400">
        <p className="text-lg">No jobs matching your filters yet.</p>
        <p className="text-sm">New postings will appear here automatically.</p>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {jobs.map((job) => {
        const salary = formatSalary(job);
        const applied = job.application_status === "applied";
        return (
          <li
            key={job.id}
            className={`bg-white border rounded-[10px] p-4 transition-colors hover:bg-[#fafbfc] ${
              applied ? "border-emerald-200 border-l-4 border-l-emerald-400" : "border-line"
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <button
                  type="button"
                  onClick={() => onOpen && onOpen(job)}
                  title="View details"
                  className="text-left font-medium text-[14.5px] text-ink hover:text-brand-600 hover:underline"
                >
                  {job.title}
                </button>
                <p className="text-[13.5px] text-ink-500">
                  {job.company || "Unknown company"} · {job.location}
                  {salary && <span className="font-mono text-ink-400"> · {salary}</span>}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <ScoreBadge score={job.score ?? 0} />
                <ResumeBadge score={job.resume_match_score} />
                <span className="font-mono text-[11px] text-ink-400">{job.source}</span>
              </div>
            </div>

            <div className="mt-2 flex flex-wrap gap-1.5">
              <ApplicationChip status={job.application_status} />
              <Chip tone="blue">{job.seniority || "unknown"}</Chip>
              {job.is_remote && <Chip tone="green">remote</Chip>}
              {job.is_worldwide && <Chip tone="green">worldwide</Chip>}
              <Chip tone={job.pakistan_eligible ? "green" : "rose"}>
                {job.pakistan_eligible ? "Pakistan-eligible" : "location-restricted"}
              </Chip>
              <Chip tone={eligibilityTone(job)}>
                <span title="Best-guess eligible region extracted from the listing text">🌍 {eligibilityLabel(job)}</span>
              </Chip>
              {!job.is_sqa_relevant && <Chip tone="rose">low SQA relevance</Chip>}
            </div>

            {job.required_skills?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {job.required_skills.map((skill) => (
                  <span
                    key={skill}
                    className="rounded-md border border-line bg-[#fafbfc] px-2 py-0.5 font-mono text-[11.5px] text-ink-600"
                  >
                    {skill}
                  </span>
                ))}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <details className="min-w-0 text-xs text-ink-400">
                <summary className="cursor-pointer select-none">
                  Matched: "{job.matched_keyword}" · discovered {new Date(job.discovered_at).toLocaleString()}
                </summary>
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(job.score_breakdown || {}).map(([name, value]) => (
                  <span key={name} className="rounded bg-[#f1f3f5] px-2 py-1 font-mono text-[11px] text-ink-600">
                    {name.replaceAll("_", " ")}: {value}
                  </span>
                ))}
              </div>
              </details>
              <ApplicationActions job={job} onStatusChange={onStatusChange} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
