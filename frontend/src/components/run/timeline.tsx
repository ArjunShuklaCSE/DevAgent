"use client";

import type { Step } from "@/lib/api";
import { formatDuration, humanize } from "@/lib/format";

import { cx } from "../ui";

const DOT: Record<Step["status"], string> = {
  running: "bg-sky-500 animate-pulse",
  completed: "bg-emerald-500",
  failed: "bg-red-500",
  skipped: "bg-zinc-400",
};

export function Timeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0)
    return <p className="p-4 text-sm text-zinc-500">Waiting for the worker to pick up the run.</p>;
  return (
    <ol className="flex flex-col p-2" aria-label="Run timeline">
      {steps.map((step, index) => (
        <li
          key={step.id}
          className="relative flex gap-3 rounded-md px-2 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
        >
          {index < steps.length - 1 && (
            <span
              aria-hidden
              className="absolute top-5 bottom-[-0.5rem] left-[0.9rem] w-px bg-zinc-200 dark:bg-zinc-800"
            />
          )}
          <span
            aria-hidden
            className={cx("relative mt-1.5 size-2.5 shrink-0 rounded-full", DOT[step.status])}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm font-medium">
                {humanize(step.state)}
                {step.status === "failed" && (
                  <span className="ml-2 text-xs font-normal text-red-500">failed</span>
                )}
              </p>
              <span className="font-mono text-xs text-zinc-500 tabular-nums">
                {step.status === "running" ? "running" : formatDuration(step.duration_ms)}
              </span>
            </div>
            {step.summary && (
              <p className="mt-0.5 text-xs break-words text-zinc-600 dark:text-zinc-400">
                {step.summary}
              </p>
            )}
            {step.error && (
              <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">
                {step.error.code}: {step.error.message}
              </p>
            )}
            {step.rationale && (
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-zinc-500 select-none">Rationale</summary>
                <p className="mt-1 whitespace-pre-wrap text-zinc-600 dark:text-zinc-400">
                  {step.rationale}
                </p>
              </details>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
