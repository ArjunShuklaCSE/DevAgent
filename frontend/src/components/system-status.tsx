import type { SystemHealth } from "@/lib/health";

const LABELS: Record<string, string> = { database: "PostgreSQL", redis: "Redis" };

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-2 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
    />
  );
}

export function SystemStatus({ health }: { health: SystemHealth }) {
  if (health.kind === "unreachable") {
    return (
      <section
        role="alert"
        className="rounded-md border border-red-900/60 bg-red-950/30 p-4 text-sm"
      >
        <p className="font-medium text-red-300">API unreachable</p>
        <p className="mt-1 text-red-200/80">{health.message}</p>
      </section>
    );
  }

  const { report } = health;
  const entries = Object.entries(report.checks);
  const healthy = report.status === "ok";

  return (
    <section aria-label="System status" className="rounded-md border border-zinc-800 text-sm">
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <StatusDot ok={healthy} />
          <span className="font-medium">{healthy ? "All systems operational" : "Degraded"}</span>
        </div>
        <span className="font-mono text-xs text-zinc-500">api v{report.version}</span>
      </header>
      <ul className="divide-y divide-zinc-800">
        {entries.length === 0 ? (
          <li className="px-4 py-3 text-zinc-500">No data yet</li>
        ) : (
          entries.map(([name, check]) => (
            <li key={name} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <StatusDot ok={check.status === "ok"} />
                <span>{LABELS[name] ?? name}</span>
              </div>
              <span className="font-mono text-xs text-zinc-400">
                {check.status === "ok" ? `${check.latency_ms.toFixed(1)} ms` : check.error}
              </span>
            </li>
          ))
        )}
      </ul>
    </section>
  );
}
