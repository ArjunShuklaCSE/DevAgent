"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { Badge, Card, EmptyState, ErrorState, Loading, Stat, cx } from "@/components/ui";
import { api, type EvalCaseResult, type EvalRunDetail, type EvalRunSummary } from "@/lib/api";
import { formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";

import { FailureChart, ResolveRateChart } from "./charts";
import { FAILURE_LABELS, rateLabel, runName } from "./format";

export function EvaluationView() {
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: api.evalRuns });
  const [selected, setSelected] = useState<string | null>(null);

  if (runs.isPending) return <Loading label="Loading evaluation runs" />;
  if (runs.isError) return <ErrorState title="Could not load evaluation runs" error={runs.error} />;
  const latest = runs.data[0];
  if (latest === undefined) {
    return (
      <Card>
        <EmptyState title="No data yet">
          No evaluation has been run. Start one with{" "}
          <code className="font-mono">devagent eval run --model &lt;model&gt;</code>; its results
          appear here.
        </EmptyState>
      </Card>
    );
  }
  const current = selected ?? latest.id;
  return (
    <div className="flex flex-col gap-6">
      <RunPicker runs={runs.data} selected={current} onSelect={setSelected} />
      {runs.data.length > 1 && (
        <Card title="Resolve rate by run">
          <div className="p-4">
            <ResolveRateChart runs={runs.data} />
            <p className="mt-2 text-xs text-zinc-500">
              Dots are the observed rate; bands are the 95% Wilson interval. With few cases the
              bands are wide, so small differences are not meaningful.
            </p>
          </div>
        </Card>
      )}
      <RunDetail id={current} />
    </div>
  );
}

function RunPicker({
  runs,
  selected,
  onSelect,
}: {
  runs: EvalRunSummary[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <Card title="Evaluation runs">
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
        {runs.map((run) => {
          const rate = rateLabel(run.resolved);
          return (
            <li key={run.id}>
              <button
                type="button"
                onClick={() => onSelect(run.id)}
                aria-pressed={run.id === selected}
                className={cx(
                  "flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-900/60",
                  run.id === selected && "bg-zinc-50 dark:bg-zinc-900/60",
                )}
              >
                <span className="font-medium">{runName(run)}</span>
                <span className="font-mono text-xs text-zinc-500">
                  {run.dataset} v{run.dataset_version} · {run.model}
                </span>
                <span className="ml-auto font-mono text-xs tabular-nums">
                  resolved {rate.value}
                </span>
                <span className="text-xs text-zinc-500">{formatTime(run.started_at)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function RunDetail({ id }: { id: string }) {
  const detail = useQuery({ queryKey: ["eval-run", id], queryFn: () => api.evalRun(id) });
  if (detail.isPending) return <Loading label="Loading results" />;
  if (detail.isError)
    return <ErrorState title="Could not load this evaluation run" error={detail.error} />;
  const run = detail.data;
  const resolved = rateLabel(run.resolved);
  const applied = rateLabel(run.patch_applied);
  const reproduced = rateLabel(run.reproduction_created);
  const budget = run.config.budget ?? {};
  return (
    <div className="flex flex-col gap-6">
      {run.model === "scripted" && (
        <div
          role="note"
          className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
        >
          This run used the <code className="font-mono">scripted</code> model, which replays a
          recorded cassette. It shows the harness and scoring working end to end, not how well a
          real model solves issues.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Resolved" value={resolved.value} hint={resolved.hint} />
        <Stat label="Patch applied" value={applied.value} hint={applied.hint} />
        <Stat label="Reproduction test created" value={reproduced.value} hint={reproduced.hint} />
        <Stat
          label="Mean debug retries"
          value={run.mean_retries === null ? "n/a" : run.mean_retries.toFixed(2)}
          hint={`max ${budget.max_fix_attempts ?? "default"} per run`}
        />
        <Stat label="Median steps" value={run.median_steps ?? "n/a"} />
        <Stat label="Median wall clock" value={formatDuration(run.median_wall_clock_ms)} />
        <Stat
          label="Median tokens"
          value={run.median_tokens === null ? "n/a" : formatTokens(run.median_tokens)}
        />
        <Stat
          label="Total cost"
          value={formatCost(run.total_cost_usd)}
          hint={`${formatCost(run.mean_cost_usd)} per case`}
        />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Failure categories">
          {Object.keys(run.failures).length === 0 ? (
            <EmptyState title="No failures" />
          ) : (
            <div className="p-4">
              <FailureChart failures={run.failures} />
            </div>
          )}
        </Card>
        <Card title="Run configuration">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 p-4 text-sm">
            <dt className="text-zinc-500">Dataset</dt>
            <dd className="font-mono">
              {run.dataset} v{run.dataset_version}
            </dd>
            <dt className="text-zinc-500">Model</dt>
            <dd className="font-mono">{run.model}</dd>
            <dt className="text-zinc-500">Budget</dt>
            <dd className="font-mono text-xs">
              {Object.entries(budget)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(", ") || "defaults"}
            </dd>
            <dt className="text-zinc-500">Prompts</dt>
            <dd className="font-mono text-xs">
              {Object.entries(run.prompt_versions)
                .map(([k, v]) => `${k} ${v}`)
                .join(", ") || "n/a"}
            </dd>
            <dt className="text-zinc-500">By difficulty</dt>
            <dd className="flex flex-wrap gap-2">
              {Object.entries(run.by_difficulty).map(([difficulty, rate]) => (
                <Badge key={difficulty}>
                  {difficulty}: {rateLabel(rate).value}
                </Badge>
              ))}
            </dd>
            {run.skipped.length > 0 && (
              <>
                <dt className="text-zinc-500">Not run</dt>
                <dd className="text-xs text-zinc-500">
                  {run.skipped.join(", ")} (no recorded cassette for this model)
                </dd>
              </>
            )}
          </dl>
        </Card>
      </div>
      <CaseTable run={run} />
    </div>
  );
}

function Outcome({ result }: { result: EvalCaseResult }) {
  if (result.resolved) return <Badge tone="success">Resolved</Badge>;
  const category = result.failure_category;
  return <Badge tone="danger">{category ? FAILURE_LABELS[category] : "Unresolved"}</Badge>;
}

function CaseTable({ run }: { run: EvalRunDetail }) {
  return (
    <Card title={`Cases (${run.n})`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="px-4 py-2 font-medium">Case</th>
              <th className="px-4 py-2 font-medium">Outcome</th>
              <th className="px-4 py-2 font-medium">Agent run</th>
              <th className="px-4 py-2 text-right font-medium">Hidden tests</th>
              <th className="px-4 py-2 text-right font-medium">Regressions</th>
              <th className="px-4 py-2 text-right font-medium">Retries</th>
              <th className="px-4 py-2 text-right font-medium">Steps</th>
              <th className="px-4 py-2 text-right font-medium">Time</th>
              <th className="px-4 py-2 text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {run.results.map((result) => (
              <tr key={`${result.case_id}-${result.repeat}`}>
                <td className="px-4 py-2.5">
                  <span className="font-medium">{result.case_id}</span>
                  <span className="ml-2 text-xs text-zinc-500">{result.difficulty}</span>
                </td>
                <td className="px-4 py-2.5">
                  <Outcome result={result} />
                </td>
                <td className="px-4 py-2.5">
                  {result.agent_run_id ? (
                    <Link
                      href={`/runs/${result.agent_run_id}`}
                      className="inline-flex items-center gap-2 hover:underline"
                    >
                      {result.run_status && <StatusBadge status={result.run_status} />}
                      <span className="font-mono text-xs">{result.agent_run_id.slice(0, 8)}</span>
                    </Link>
                  ) : (
                    "-"
                  )}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                  {result.fail_to_pass_passed}/{result.fail_to_pass_total}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                  {result.pass_to_pass_regressions}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">{result.retries}</td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">{result.steps}</td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                  {formatDuration(result.wall_clock_ms)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                  {formatCost(result.cost_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
