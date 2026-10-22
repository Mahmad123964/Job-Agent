import { useEffect, useState } from "react";

export default function Filters({ filters, setFilters, sources, onRefresh, onPollNow }) {
  const [minScore, setMinScore] = useState(Number(filters.min_score) || 0);
  useEffect(() => setMinScore(Number(filters.min_score) || 0), [filters.min_score]);

  const commitMinScore = () => {
    const next = minScore === 0 ? "" : String(minScore);
    if (next !== filters.min_score) setFilters({ ...filters, min_score: next });
  };

  return (
    <div className="bg-white border border-line rounded-[10px] p-4 flex flex-wrap gap-3 items-end">
      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Keyword</label>
        <input
          type="text"
          value={filters.keyword}
          onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}
          placeholder="e.g. QA, SDET"
          className="border border-line rounded-lg bg-white px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 w-40"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Seniority</label>
        <select
          value={filters.seniority}
          onChange={(e) => setFilters({ ...filters, seniority: e.target.value })}
          className="border border-line rounded-lg bg-white px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 w-32"
        >
          <option value="">All levels</option>
          {["junior", "mid", "senior", "lead", "unknown"].map((level) => (
            <option key={level} value={level}>{level}</option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">
          Minimum score: <span className="font-mono normal-case tracking-normal text-ink">{minScore}</span>
        </label>
        {/* Local state while dragging; the feed only refetches on release. */}
        <input
          type="range"
          min="0"
          max="100"
          step="5"
          value={minScore}
          onChange={(e) => setMinScore(Number(e.target.value))}
          onPointerUp={commitMinScore}
          onKeyUp={commitMinScore}
          onBlur={commitMinScore}
          className="w-40 accent-brand-600"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Sort</label>
        <select
          value={filters.sort}
          onChange={(e) => setFilters({ ...filters, sort: e.target.value })}
          className="border border-line rounded-lg bg-white px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 w-28"
        >
          <option value="score">Score</option>
          <option value="resume">Resume match</option>
          <option value="newest">Newest</option>
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Location</label>
        <input
          type="text"
          value={filters.location}
          onChange={(e) => setFilters({ ...filters, location: e.target.value })}
          placeholder="e.g. Europe, Worldwide"
          className="border border-line rounded-lg bg-white px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 w-40"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11.5px] uppercase tracking-[0.06em] text-ink-400">Source</label>
        <select
          value={filters.source}
          onChange={(e) => setFilters({ ...filters, source: e.target.value })}
          className="border border-line rounded-lg bg-white px-3 py-1.5 text-[13.5px] text-ink focus:outline-none focus:border-ink-400 w-40"
        >
          <option value="">All sources</option>
          {sources.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      <button
        onClick={onRefresh}
        className="bg-ink hover:bg-navy text-white text-[13.5px] font-medium px-4 py-1.5 rounded-lg"
      >
        Apply
      </button>
      <button
        onClick={onPollNow}
        className="border border-brand-600 text-brand-600 hover:bg-brand-50 text-[13.5px] font-medium px-4 py-1.5 rounded-lg"
      >
        Check now
      </button>
    </div>
  );
}
