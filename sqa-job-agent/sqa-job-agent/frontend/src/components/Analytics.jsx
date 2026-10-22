// Feature 5: small trends dashboard over GET /api/analytics. Plain inline SVG
// bars — no chart library (three is the only heavy dependency and it's lazy).

function BarChart({ data, valueKey, labelKey, format = (v) => v, height = 140, accent = "#22d3ee", emptyLabel = "No data" }) {
  const values = data.map((d) => d[valueKey] ?? 0);
  const max = Math.max(1, ...values);
  if (data.length === 0) return <p className="text-sm text-ink-400">{emptyLabel}</p>;
  const barW = 100 / data.length;
  return (
    <div>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="h-36 w-full" role="img" aria-label="bar chart">
        {data.map((d, i) => {
          const v = d[valueKey];
          const h = v == null ? 0 : (v / max) * (height - 18);
          return (
            <g key={i}>
              <rect
                x={i * barW + barW * 0.15}
                y={height - 14 - h}
                width={barW * 0.7}
                height={h}
                rx="0.6"
                fill={v == null ? "#e2e6ea" : accent}
                opacity={v == null ? 0.6 : 0.9}
              >
                <title>{`${d[labelKey]}: ${v == null ? "—" : format(v)}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-ink-400">
        <span>{data[0][labelKey]}</span>
        {data.length > 2 && <span>{data[Math.floor(data.length / 2)][labelKey]}</span>}
        <span>{data[data.length - 1][labelKey]}</span>
      </div>
    </div>
  );
}

function HBars({ data }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  if (data.length === 0) return <p className="text-sm text-ink-400">No jobs saved in the last 7 days.</p>;
  return (
    <ul className="space-y-1.5">
      {data.map((d) => (
        <li key={d.source} className="grid grid-cols-[110px_1fr_36px] items-center gap-2 text-[12.5px]">
          <span className="truncate font-mono text-ink-600">{d.source}</span>
          <div className="h-3 rounded bg-[#f1f3f5]">
            <div className="h-3 rounded bg-brand-500" style={{ width: `${(d.count / max) * 100}%` }} />
          </div>
          <span className="text-right font-mono text-ink-500">{d.count}</span>
        </li>
      ))}
    </ul>
  );
}

function Card({ title, subtitle, children }) {
  return (
    <section className="rounded-[10px] border border-line bg-white p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="font-display text-[13px] font-semibold text-ink">{title}</h3>
        {subtitle && <span className="text-xs text-ink-400">{subtitle}</span>}
      </div>
      {children}
    </section>
  );
}

export default function Analytics({ data, loading }) {
  if (loading && !data) return <p className="text-sm text-ink-500">Loading analytics…</p>;
  if (!data) return <p className="text-sm text-ink-400">Analytics unavailable.</p>;
  const c = data.conversion || {};
  const trend = (data.score_trend || []).map((d) => ({ ...d, label: d.day.slice(5) }));
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="Applications" subtitle="all time">
        <div className="grid grid-cols-3 gap-3">
          {[
            ["Saved", c.saved_total ?? 0, "text-ink"],
            ["Applied", c.applied ?? 0, "text-emerald-700"],
            ["Conversion", `${Math.round((c.rate ?? 0) * 100)}%`, "text-brand-600"],
          ].map(([label, value, tone]) => (
            <div key={label} className="rounded-lg bg-[#fafbfc] px-3 py-2.5">
              <div className={`font-display text-2xl font-semibold tracking-tight ${tone}`}>{value}</div>
              <div className="text-[11px] uppercase tracking-[0.04em] text-ink-400">{label}</div>
            </div>
          ))}
        </div>
        {c.applying > 0 && <p className="mt-2 text-xs text-ink-400">{c.applying} more in progress ("applying").</p>}
      </Card>
      <Card title="Jobs per source" subtitle="last 7 days">
        <HBars data={data.jobs_per_source || []} />
      </Card>
      <Card title="Average score per day" subtitle="last 14 days · grey = no jobs that day">
        <BarChart data={trend} valueKey="avg_score" labelKey="label" format={(v) => `${v} avg`} />
      </Card>
      <Card title="Jobs saved per day" subtitle="last 14 days">
        <BarChart data={trend} valueKey="count" labelKey="label" accent="#0f7f8c" format={(v) => `${v} job(s)`} />
      </Card>
    </div>
  );
}
