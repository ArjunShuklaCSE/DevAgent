"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";

import { api, TERMINAL_STATUSES, type Run, type RunStatus } from "@/lib/api";
import { useRunEvents, type ConnectionState, type RunEvent } from "@/lib/events";
import {
  elapsedMs,
  formatCost,
  formatDuration,
  formatTokens,
  humanize,
  shortSha,
} from "@/lib/format";

import { runRepoName } from "../recent-runs";
import { StatusBadge } from "../status-badge";
import { Badge, Button, Card, ErrorState, Loading, Stat, Tabs } from "../ui";
import { LlmCallsPanel, PlanPanel, TestsPanel, ToolCallsPanel } from "./panels";
import { Terminal } from "./terminal";
import { Timeline } from "./timeline";

type Tab = "terminal" | "tools" | "llm" | "plan" | "tests";

const isFinalEvent = (event: RunEvent) =>
  event.type === "status_changed" && TERMINAL_STATUSES.has(event.payload.to_status as RunStatus);

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function ConnectionIndicator({ state }: { state: ConnectionState }) {
  const map = {
    connecting: { tone: "neutral", label: "Connecting" },
    live: { tone: "success", label: "Live" },
    reconnecting: { tone: "warning", label: "Reconnecting" },
    closed: { tone: "neutral", label: "Finished" },
  } as const;
  const { tone, label } = map[state];
  return <Badge tone={tone}>{label}</Badge>;
}

export function RunView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId) });
  const steps = useQuery({ queryKey: ["steps", runId], queryFn: () => api.steps(runId) });
  const terminal = run.data ? TERMINAL_STATUSES.has(run.data.status) : false;

  const { events, state } = useRunEvents(runId, {
    enabled: run.isSuccess,
    isFinal: isFinalEvent,
    onEvent: (event) => {
      if (event.type === "status_changed" || event.type === "llm_usage" || event.type === "error") {
        void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      }
      if (event.type.startsWith("step_") || event.type === "status_changed") {
        void queryClient.invalidateQueries({ queryKey: ["steps", runId] });
      }
      if (event.type === "tool_call")
        void queryClient.invalidateQueries({ queryKey: ["tool-calls", runId] });
      if (event.type === "llm_usage")
        void queryClient.invalidateQueries({ queryKey: ["llm-calls", runId] });
      if (event.type === "test_result")
        void queryClient.invalidateQueries({ queryKey: ["test-runs", runId] });
    },
  });
  const [tab, setTab] = useState<Tab>("terminal");
  const now = useNow(!terminal);

  const cancel = useMutation({
    mutationFn: () => api.cancel(runId),
    onSuccess: (data) => queryClient.setQueryData(["run", runId], data),
  });

  if (run.isPending) return <Loading label="Loading run" />;
  if (run.isError) return <ErrorState title="Could not load this run" error={run.error} />;
  const data = run.data;
  const live = !terminal && data.status !== "awaiting_approval";

  return (
    <div className="flex flex-col gap-6">
      <RunHeader
        run={data}
        connection={state}
        onCancel={() => cancel.mutate()}
        cancelling={cancel.isPending}
      />
      {cancel.isError && <ErrorState title="Could not cancel" error={cancel.error} />}
      {state === "reconnecting" && (
        <p
          role="status"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
        >
          Lost the live connection. Reconnecting and replaying any missed events…
        </p>
      )}
      {data.result.failure && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm dark:border-red-900/60 dark:bg-red-950/30"
        >
          <p className="font-medium text-red-700 dark:text-red-300">
            {humanize(data.status)}: {data.result.failure.code}
          </p>
          <p className="mt-1 text-red-600 dark:text-red-200/80">{data.result.failure.message}</p>
          <p className="mt-1 text-xs text-red-600/80 dark:text-red-300/70">
            Failure category: {data.result.failure.category}
          </p>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Elapsed"
          value={formatDuration(elapsedMs(data.started_at, data.finished_at, now))}
        />
        <Stat
          label="Steps"
          value={data.step_count}
          hint={data.config.budget ? `of ${data.config.budget.max_steps}` : undefined}
        />
        <Stat
          label="Fix attempts"
          value={data.fix_attempts}
          hint={data.config.budget ? `of ${data.config.budget.max_fix_attempts}` : undefined}
        />
        <Stat
          label="Tokens"
          value={formatTokens(data.input_tokens + data.output_tokens)}
          hint={`${formatTokens(data.input_tokens)} in / ${formatTokens(data.output_tokens)} out`}
        />
        <Stat
          label="Cost"
          value={formatCost(data.cost_usd)}
          hint={
            data.config.budget ? `limit ${formatCost(data.config.budget.max_cost_usd)}` : undefined
          }
        />
        <Stat
          label="Model"
          value={<span className="text-sm">{String(data.config.model ?? "–")}</span>}
          hint={data.config.provider ? String(data.config.provider) : undefined}
        />
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[22rem_1fr]">
        <Card title="Timeline">
          {steps.isPending ? (
            <Loading />
          ) : steps.isError ? (
            <div className="p-4">
              <ErrorState error={steps.error} />
            </div>
          ) : (
            <Timeline steps={steps.data} />
          )}
        </Card>
        <Card className="min-w-0">
          <Tabs<Tab>
            active={tab}
            onChange={setTab}
            tabs={[
              { id: "terminal", label: "Terminal" },
              { id: "tools", label: "Tool calls" },
              { id: "llm", label: "LLM calls" },
              { id: "plan", label: "Plan" },
              { id: "tests", label: "Tests" },
            ]}
          />
          {tab === "terminal" && <Terminal events={events} />}
          {tab === "tools" && <ToolCallsPanel runId={runId} live={live} />}
          {tab === "llm" && <LlmCallsPanel runId={runId} live={live} />}
          {tab === "plan" && <PlanPanel run={data} />}
          {tab === "tests" && <TestsPanel runId={runId} live={live} />}
        </Card>
      </div>
      <RunFacts run={data} />
    </div>
  );
}

function RunHeader({
  run,
  connection,
  onCancel,
  cancelling,
}: {
  run: Run;
  connection: ConnectionState;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const terminal = TERMINAL_STATUSES.has(run.status);
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="font-mono text-xs text-zinc-500">
          <Link href="/runs" className="hover:underline">
            Runs
          </Link>{" "}
          / {runRepoName(run)}
          {run.issue.number !== null && ` #${run.issue.number}`}
        </p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">{run.issue.title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <StatusBadge status={run.status} />
          <ConnectionIndicator state={terminal ? "closed" : connection} />
          {run.mode === "dry_run" && <Badge>dry run</Badge>}
          {run.injection_flags.length > 0 && (
            <Badge tone="warning">
              <span
                title={run.injection_flags
                  .map((f) => `${f.label} (${f.source}:${f.line})`)
                  .join("\n")}
              >
                {run.injection_flags.length} prompt-injection{" "}
                {run.injection_flags.length === 1 ? "flag" : "flags"}
              </span>
            </Badge>
          )}
        </div>
        {run.status_reason && <p className="mt-2 text-sm text-zinc-500">{run.status_reason}</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        {run.final_diff_sha256 && (
          <Link
            href={`/runs/${run.id}/review`}
            className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            {run.status === "awaiting_approval" ? "Review and approve" : "View diff"}
          </Link>
        )}
        {!terminal && run.status !== "approved" && (
          <Button variant="danger" onClick={onCancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel run"}
          </Button>
        )}
      </div>
    </div>
  );
}

function RunFacts({ run }: { run: Run }) {
  const rows: [string, string][] = [
    ["Base commit", shortSha(run.base_commit_sha)],
    ["Sandbox image", shortSha(run.sandbox_image_digest?.replace("sha256:", ""), 16)],
    ["Diff sha256", shortSha(run.final_diff_sha256, 16)],
    ...Object.entries(run.prompt_versions).map(([name, version]): [string, string] => [
      `Prompt ${name}`,
      String(version),
    ]),
  ];
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-zinc-500 select-none">Reproducibility</summary>
      <dl className="mt-2 grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 font-mono text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="font-sans text-zinc-500">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
