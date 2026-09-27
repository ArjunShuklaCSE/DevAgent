/** Formatting helpers shared by the dashboard. All inputs come from API data. */

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${(minutes % 60).toString().padStart(2, "0")}m`;
}

export function elapsedMs(
  start: string | null,
  end: string | null,
  now: number = Date.now(),
): number | null {
  if (!start) return null;
  const from = Date.parse(start);
  const to = end ? Date.parse(end) : now;
  return Number.isNaN(from) || Number.isNaN(to) ? null : Math.max(0, to - from);
}

export function formatCost(usd: string | number | null | undefined): string {
  const value = typeof usd === "string" ? Number.parseFloat(usd) : usd;
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
  return `${(count / 1_000_000).toFixed(2)}M`;
}

export function formatTime(iso: string | null): string {
  if (!iso) return "–";
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function humanize(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortSha(sha: string | null | undefined, length = 12): string {
  return sha ? sha.slice(0, length) : "–";
}
