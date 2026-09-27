"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { api, type RunSummary } from "@/lib/api";
import { elapsedMs, formatCost, formatDuration, formatTime } from "@/lib/format";

import { StatusBadge } from "./status-badge";
import { EmptyState, ErrorState, Loading } from "./ui";

export function runRepoName(run: Pick<RunSummary, "repository">) {
  const repo = run.repository;
  return repo.source === "local" ? repo.name : `${repo.owner}/${repo.name}`;
}

export function RunsTable({ status, limit }: { status?: string; limit?: number }) {
  const runs = useQuery({
    queryKey: ["runs", status ?? "all"],
    queryFn: () => api.runs(status),
    refetchInterval: 3_000,
  });
  if (runs.isPending) return <Loading label="Loading runs" />;
  if (runs.isError)
    return (
      <div className="p-4">
        <ErrorState title="Could not load runs" error={runs.error} />
      </div>
    );
  const items = limit ? runs.data.items.slice(0, limit) : runs.data.items;
  if (items.length === 0) {
    return (
      <EmptyState title={status ? "No runs with this status" : "No runs yet"}>
        Start a run from the home page to see it here.
      </EmptyState>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th className="px-4 py-2 font-medium">Issue</th>
            <th className="px-4 py-2 font-medium">Repository</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 text-right font-medium">Steps</th>
            <th className="px-4 py-2 text-right font-medium">Cost</th>
            <th className="px-4 py-2 text-right font-medium">Duration</th>
            <th className="px-4 py-2 font-medium">Started</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {items.map((run) => (
            <tr key={run.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/60">
              <td className="max-w-md px-4 py-2.5">
                <Link
                  href={`/runs/${run.id}`}
                  className="block truncate font-medium hover:underline"
                >
                  {run.issue.number !== null && (
                    <span className="text-zinc-500">#{run.issue.number} </span>
                  )}
                  {run.issue.title}
                </Link>
                {run.mode === "dry_run" && <span className="text-xs text-zinc-500">dry run</span>}
              </td>
              <td className="px-4 py-2.5 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                {runRepoName(run)}
              </td>
              <td className="px-4 py-2.5">
                <StatusBadge status={run.status} />
              </td>
              <td className="px-4 py-2.5 text-right font-mono tabular-nums">{run.step_count}</td>
              <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                {formatCost(run.cost_usd)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                {formatDuration(elapsedMs(run.started_at, run.finished_at))}
              </td>
              <td className="px-4 py-2.5 text-xs whitespace-nowrap text-zinc-500">
                {formatTime(run.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
