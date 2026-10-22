// The API returns UTC timestamps without a zone suffix; treat them as UTC.
export function parseUtc(iso) {
  if (!iso) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function relativeTime(iso) {
  const ts = parseUtc(iso);
  if (!ts) return "unknown time";
  const seconds = Math.round((Date.now() - ts.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [name, size] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      return `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  return `${seconds} seconds ago`;
}

export function absoluteTime(iso) {
  const ts = parseUtc(iso);
  return ts ? ts.toLocaleString() : "";
}

export function isPast(iso) {
  const ts = parseUtc(iso);
  return !!ts && ts.getTime() <= Date.now();
}
