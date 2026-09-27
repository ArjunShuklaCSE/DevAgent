"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { EvalRunSummary, FailureCategory } from "@/lib/api";

import { FAILURE_LABELS, percent, runName } from "./format";

const AXIS = { fontSize: 12, fill: "var(--chart-axis)" };
const TOOLTIP = {
  contentStyle: {
    background: "var(--chart-tooltip-bg)",
    border: "1px solid var(--chart-grid)",
    borderRadius: 6,
    fontSize: 12,
  },
  cursor: { fill: "var(--chart-cursor)" },
};

export function FailureChart({ failures }: { failures: Partial<Record<FailureCategory, number>> }) {
  const data = Object.entries(failures).map(([category, count]) => ({
    name: FAILURE_LABELS[category as FailureCategory] ?? category,
    count,
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 36 + 40)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24 }}>
        <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
        <XAxis type="number" allowDecimals={false} tick={AXIS} stroke="var(--chart-grid)" />
        <YAxis type="category" dataKey="name" width={130} tick={AXIS} stroke="var(--chart-grid)" />
        <Tooltip {...TOOLTIP} />
        <Bar
          dataKey="count"
          name="Cases"
          fill="var(--chart-failure)"
          radius={[0, 4, 4, 0]}
          maxBarSize={28}
          isAnimationActive={false}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

/**
 * Resolve rate per evaluation run: a dot at the observed rate and a band for the 95%
 * Wilson interval, so a 0% run and the uncertainty of small samples are both visible.
 */
export function ResolveRateChart({ runs }: { runs: EvalRunSummary[] }) {
  return (
    <div className="flex flex-col gap-3" role="list" aria-label="Resolve rate by run">
      {runs.map((run) => {
        const { value, ci_low: low, ci_high: high, k, n } = run.resolved;
        return (
          <div
            key={run.id}
            role="listitem"
            className="grid grid-cols-[10rem_1fr_7rem] items-center gap-3"
          >
            <span className="truncate text-sm">{runName(run)}</span>
            <div className="relative h-6 rounded bg-zinc-100 dark:bg-zinc-800/70">
              {[0.25, 0.5, 0.75].map((tick) => (
                <span
                  key={tick}
                  className="absolute inset-y-0 w-px bg-zinc-200 dark:bg-zinc-700/60"
                  style={{ left: `${tick * 100}%` }}
                />
              ))}
              {n > 0 && (
                <>
                  <span
                    title={`95% CI ${percent(low)} to ${percent(high)}`}
                    className="absolute inset-y-1 rounded bg-emerald-500/25 dark:bg-emerald-400/25"
                    style={{
                      left: `${low * 100}%`,
                      width: `${Math.max(0.5, (high - low) * 100)}%`,
                    }}
                  />
                  <span
                    className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-500 ring-2 ring-white dark:bg-emerald-400 dark:ring-zinc-900"
                    style={{ left: `${(value ?? 0) * 100}%` }}
                  />
                </>
              )}
            </div>
            <span className="text-right font-mono text-xs tabular-nums">
              {n > 0 ? `${percent(value)} (${k}/${n})` : "n/a"}
            </span>
          </div>
        );
      })}
      <div className="grid grid-cols-[10rem_1fr_7rem] gap-3 text-xs text-zinc-500">
        <span />
        <div className="flex justify-between font-mono">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
        <span />
      </div>
    </div>
  );
}
