"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Run } from "@/lib/api";
import { formatCost, formatDuration, humanize } from "@/lib/format";

import { Badge, EmptyState, ErrorState, Loading, cx } from "../ui";

const statusTone = (status: string) =>
  status === "ok" ? "success" : status === "denied" ? "warning" : "danger";

function Json({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto rounded bg-zinc-100 p-2 font-mono text-xs dark:bg-zinc-950">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function ToolCallsPanel({ runId, live }: { runId: string; live: boolean }) {
  const query = useQuery({
    queryKey: ["tool-calls", runId],
    queryFn: () => api.toolCalls(runId),
    refetchInterval: live ? 3000 : false,
  });
  const [open, setOpen] = useState<string | null>(null);
  if (query.isPending) return <Loading />;
  if (query.isError)
    return (
      <div className="p-4">
        <ErrorState error={query.error} />
      </div>
    );
  if (query.data.length === 0) return <EmptyState title="No tool calls yet" />;
  return (
    <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
      {query.data.map((call) => (
        <li key={call.id}>
          <button
            type="button"
            onClick={() => setOpen(open === call.id ? null : call.id)}
            className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
          >
            <span className="font-mono">{call.tool_name}</span>
            <span className="truncate font-mono text-xs text-zinc-500">
              {summarizeInput(call.input)}
            </span>
            <span className="ml-auto flex items-center gap-2">
              <Badge tone={statusTone(call.status)}>{call.status}</Badge>
              <span className="w-16 text-right font-mono text-xs text-zinc-500">
                {formatDuration(call.duration_ms)}
              </span>
            </span>
          </button>
          {open === call.id && (
            <div className="flex flex-col gap-2 px-4 pb-3">
              <Json value={call.input} />
              {call.error && (
                <p className="text-xs text-red-500">
                  {call.error.code}: {call.error.message}
                </p>
              )}
              {call.output_truncated && (
                <pre className="max-h-64 overflow-auto rounded bg-zinc-950 p-2 font-mono text-xs text-zinc-300">
                  {call.output_truncated}
                </pre>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function summarizeInput(input: Record<string, unknown>) {
  const value = input.path ?? input.pattern ?? input.symbol ?? input.test_ids ?? input.query;
  return value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
}

export function LlmCallsPanel({ runId, live }: { runId: string; live: boolean }) {
  const query = useQuery({
    queryKey: ["llm-calls", runId],
    queryFn: () => api.llmCalls(runId),
    refetchInterval: live ? 3000 : false,
  });
  if (query.isPending) return <Loading />;
  if (query.isError)
    return (
      <div className="p-4">
        <ErrorState error={query.error} />
      </div>
    );
  if (query.data.length === 0) return <EmptyState title="No model calls yet" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th className="px-4 py-2 font-medium">Component</th>
            <th className="px-4 py-2 font-medium">Model</th>
            <th className="px-4 py-2 font-medium">Prompt</th>
            <th className="px-4 py-2 text-right font-medium">In</th>
            <th className="px-4 py-2 text-right font-medium">Out</th>
            <th className="px-4 py-2 text-right font-medium">Cost</th>
            <th className="px-4 py-2 text-right font-medium">Latency</th>
            <th className="px-4 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-200 font-mono text-xs dark:divide-zinc-800">
          {query.data.map((call) => (
            <tr key={call.id}>
              <td className="px-4 py-2 font-sans text-sm">{humanize(call.component)}</td>
              <td className="px-4 py-2">{call.model}</td>
              <td className="px-4 py-2 text-zinc-500">{call.prompt_version.slice(0, 11)}</td>
              <td className="px-4 py-2 text-right tabular-nums">{call.input_tokens}</td>
              <td className="px-4 py-2 text-right tabular-nums">{call.output_tokens}</td>
              <td className="px-4 py-2 text-right tabular-nums">{formatCost(call.cost_usd)}</td>
              <td className="px-4 py-2 text-right tabular-nums">
                {formatDuration(call.latency_ms)}
              </td>
              <td className="px-4 py-2">
                <Badge tone={statusTone(call.status)}>{call.status}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TestsPanel({ runId, live }: { runId: string; live: boolean }) {
  const query = useQuery({
    queryKey: ["test-runs", runId],
    queryFn: () => api.testRuns(runId),
    refetchInterval: live ? 3000 : false,
  });
  if (query.isPending) return <Loading />;
  if (query.isError)
    return (
      <div className="p-4">
        <ErrorState error={query.error} />
      </div>
    );
  if (query.data.length === 0) return <EmptyState title="No test runs yet" />;
  return (
    <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
      {query.data.map((run) => {
        const bad = run.failed + run.errors;
        return (
          <li key={run.id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={run.kind === "reproduction" ? "info" : "neutral"}>
                {humanize(run.kind)}
              </Badge>
              <span className="text-emerald-600 dark:text-emerald-400">{run.passed} passed</span>
              <span className={cx(bad ? "text-red-600 dark:text-red-400" : "text-zinc-500")}>
                {run.failed} failed{run.errors ? `, ${run.errors} errors` : ""}
              </span>
              {run.timed_out && <Badge tone="danger">timed out</Badge>}
              <span className="ml-auto font-mono text-xs text-zinc-500">
                {formatDuration(run.duration_ms)}
              </span>
            </div>
            {run.results.some((r) => r.outcome !== "passed") && (
              <ul className="mt-2 flex flex-col gap-1">
                {run.results
                  .filter((r) => r.outcome !== "passed")
                  .map((r) => (
                    <li key={r.node_id} className="text-xs">
                      <span className="font-mono text-red-600 dark:text-red-400">{r.outcome}</span>{" "}
                      <span className="font-mono">{r.node_id}</span>
                      {r.message && (
                        <pre className="mt-1 max-h-32 overflow-auto rounded bg-zinc-950 p-2 font-mono text-zinc-300">
                          {r.message}
                        </pre>
                      )}
                    </li>
                  ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function PlanPanel({ run }: { run: Run }) {
  const { issue_analysis: analysis, plan, reproduction } = run.result;
  if (!analysis && !plan && !reproduction)
    return (
      <EmptyState title="No analysis yet">
        The plan appears once the agent has localized and reproduced the bug.
      </EmptyState>
    );
  return (
    <div className="grid gap-6 p-4 text-sm lg:grid-cols-2">
      {analysis && (
        <section>
          <h3 className="mb-2 font-medium">Issue analysis</h3>
          <p className="text-zinc-700 dark:text-zinc-300">{analysis.summary}</p>
          <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-zinc-500">Expected</dt>
            <dd>{analysis.expected_behavior}</dd>
            <dt className="text-zinc-500">Actual</dt>
            <dd>{analysis.current_behavior}</dd>
            <dt className="text-zinc-500">Confidence</dt>
            <dd>{analysis.confidence}</dd>
          </dl>
        </section>
      )}
      {reproduction && (
        <section>
          <h3 className="mb-2 font-medium">Reproduction</h3>
          <p className="font-mono text-xs">{reproduction.test_file}</p>
          <p className="mt-1 text-zinc-700 dark:text-zinc-300">{reproduction.explanation}</p>
          <ul className="mt-2 list-inside list-disc text-xs text-zinc-500">
            {reproduction.failing_before_fix.map((id) => (
              <li key={id} className="font-mono">
                {id}
              </li>
            ))}
          </ul>
        </section>
      )}
      {plan && (
        <section className="lg:col-span-2">
          <h3 className="mb-2 font-medium">Plan</h3>
          <ol className="list-inside list-decimal space-y-1 text-zinc-700 dark:text-zinc-300">
            {plan.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
          <p className="mt-2 text-xs text-zinc-500">
            Files: <span className="font-mono">{plan.files_to_touch.join(", ")}</span>
          </p>
          {plan.risks.length > 0 && (
            <p className="mt-1 text-xs text-zinc-500">Risks: {plan.risks.join("; ")}</p>
          )}
        </section>
      )}
    </div>
  );
}
