"use client";

import "react-diff-view/style/index.css";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Diff, Hunk, parseDiff, type ViewType } from "react-diff-view";

import { api, patchUrl, type CheckResult, type ReviewFlag, type Run } from "@/lib/api";
import { humanize } from "@/lib/format";

import { runRepoName } from "../recent-runs";
import { StatusBadge } from "../status-badge";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, inputClass } from "../ui";

const CHECK_TONE = {
  passed: "success",
  failed: "danger",
  error: "danger",
  pre_existing: "warning",
  not_configured: "neutral",
} as const;

const CHECK_LABEL: Record<CheckResult["status"], string> = {
  passed: "passed",
  failed: "failed",
  error: "could not run",
  pre_existing: "failing before too",
  not_configured: "not configured",
};

const FILE_LABEL: Record<string, string> = {
  add: "added",
  delete: "deleted",
  modify: "modified",
  rename: "renamed",
  copy: "copied",
};

const FLAG_LABEL: Record<ReviewFlag["kind"], string> = {
  sensitive_path: "Sensitive path",
  protected_path: "Protected path",
  new_dependency: "New dependency",
  network_call: "Network call",
  deleted_test: "Deleted test",
};

export function ReviewView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    // Opening the PR happens in the background after approval; follow it.
    refetchInterval: (query) =>
      query.state.data?.status === "creating_pr" ||
      (query.state.data?.status === "approved" && !query.state.data.result.delivery)
        ? 1500
        : false,
  });
  const diff = useQuery({ queryKey: ["diff", runId], queryFn: () => api.diff(runId) });
  const [viewType, setViewType] = useState<ViewType>("split");
  const [comment, setComment] = useState("");

  const files = useMemo(
    () => (diff.data?.diff ? parseDiff(diff.data.diff, { nearbySequences: "zip" }) : []),
    [diff.data],
  );

  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      decision === "approve"
        ? api.approve(runId, diff.data?.sha256 ?? "", comment)
        : api.reject(runId, comment),
    onSuccess: (updated) => {
      queryClient.setQueryData(["run", runId], updated);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  if (run.isPending || diff.isPending) return <Loading label="Loading the diff" />;
  if (run.isError) return <ErrorState title="Could not load this run" error={run.error} />;
  if (diff.isError) return <ErrorState title="Could not load the diff" error={diff.error} />;
  const data = run.data;
  const checks = diff.data.validation?.checks ?? [];
  const flags = diff.data.review_flags;
  const awaiting = data.status === "awaiting_approval";
  const pr = data.result.pull_request;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="font-mono text-xs text-zinc-500">
            <Link href="/runs" className="hover:underline">
              Runs
            </Link>{" "}
            /{" "}
            <Link href={`/runs/${runId}`} className="hover:underline">
              {runRepoName(data)}
              {data.issue.number !== null && ` #${data.issue.number}`}
            </Link>{" "}
            / review
          </p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">
            {pr?.title ?? data.issue.title}
          </h1>
          <div className="mt-2 flex items-center gap-2">
            <StatusBadge status={data.status} />
            <span className="font-mono text-xs text-zinc-500">
              {files.length} {files.length === 1 ? "file" : "files"} · sha256{" "}
              {diff.data.sha256?.slice(0, 12) ?? "–"}
            </span>
          </div>
        </div>
        <div
          className="flex gap-1 rounded-md border border-zinc-200 p-0.5 text-xs dark:border-zinc-800"
          role="group"
          aria-label="Diff layout"
        >
          {(["split", "unified"] as const).map((type) => (
            <button
              key={type}
              type="button"
              aria-pressed={viewType === type}
              onClick={() => setViewType(type)}
              className={`rounded px-2 py-1 ${viewType === type ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-500"}`}
            >
              {humanize(type)}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="flex min-w-0 flex-col gap-4">
          {files.length === 0 ? (
            <Card>
              <EmptyState title="No diff">This run has not produced a change to review.</EmptyState>
            </Card>
          ) : (
            files.map((file) => {
              const path = file.type === "delete" ? file.oldPath : file.newPath;
              return (
                <Card
                  key={`${file.oldPath}->${file.newPath}`}
                  title={<span className="font-mono text-xs">{path}</span>}
                  actions={
                    <Badge
                      tone={
                        file.type === "add"
                          ? "success"
                          : file.type === "delete"
                            ? "danger"
                            : "neutral"
                      }
                    >
                      {FILE_LABEL[file.type]}
                    </Badge>
                  }
                  className="overflow-hidden"
                >
                  <div className="diff-theme overflow-x-auto">
                    <Diff viewType={viewType} diffType={file.type} hunks={file.hunks}>
                      {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
                    </Diff>
                  </div>
                </Card>
              );
            })
          )}
        </div>

        <div className="flex flex-col gap-4">
          <Card title="Validation">
            {checks.length === 0 ? (
              <p className="p-4 text-sm text-zinc-500">No validation recorded.</p>
            ) : (
              <ul className="divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
                {checks.map((check) => (
                  <li
                    key={check.name}
                    className="flex items-center justify-between gap-2 px-4 py-2"
                  >
                    <span>{humanize(check.name)}</span>
                    <Badge tone={CHECK_TONE[check.status]}>{CHECK_LABEL[check.status]}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={`Needs a closer look (${flags.length})`}>
            {flags.length === 0 ? (
              <p className="p-4 text-sm text-zinc-500">
                Nothing flagged: no sensitive paths, new dependencies, network calls or deleted
                tests.
              </p>
            ) : (
              <ul className="divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
                {flags.map((flag, i) => (
                  <li key={i} className="px-4 py-2">
                    <Badge tone="warning">{FLAG_LABEL[flag.kind]}</Badge>
                    <p className="mt-1 font-mono text-xs">{flag.path}</p>
                    <p className="text-xs text-zinc-500">{flag.detail}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Decision">
            <div className="flex flex-col gap-3 p-4">
              {awaiting ? (
                <>
                  <textarea
                    aria-label="Review comment"
                    className={`${inputClass} min-h-20`}
                    placeholder="Optional comment (required context for a rejection helps the next run)"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    maxLength={2000}
                  />
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      className="flex-1"
                      disabled={decide.isPending || !diff.data.sha256}
                      onClick={() => decide.mutate("approve")}
                    >
                      Approve
                    </Button>
                    <Button
                      className="flex-1"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate("reject")}
                    >
                      Reject
                    </Button>
                  </div>
                  <p className="text-xs text-zinc-500">
                    Approval is bound to this exact diff (sha256 {diff.data.sha256?.slice(0, 12)}).
                    If the diff changes, you will be asked to review again.
                  </p>
                </>
              ) : ["approved", "creating_pr", "pr_created"].includes(data.status) ? (
                <Delivery run={data} />
              ) : (
                <p className="text-sm">
                  {data.status === "rejected"
                    ? "Rejected."
                    : `This run is ${humanize(data.status).toLowerCase()}; there is nothing to approve.`}
                </p>
              )}
              {decide.isError && <ErrorState title="Decision not recorded" error={decide.error} />}
            </div>
          </Card>
          {pr && (
            <Card title="Pull request description">
              <pre className="max-h-96 overflow-auto p-4 font-sans text-xs whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
                {pr.body}
              </pre>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Delivery({ run }: { run: Run }) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.publish(run.id),
    onSuccess: (updated) => {
      // Clear the old outcome so the page polls until the new attempt finishes.
      queryClient.setQueryData(["run", run.id], {
        ...updated,
        result: { ...updated.result, delivery: undefined },
      });
    },
  });
  const delivery = run.result.delivery;
  const patch = (
    <a
      href={patchUrl(run.id)}
      className="text-sm text-indigo-600 hover:underline dark:text-indigo-400"
      download
    >
      Download patch
    </a>
  );
  if (delivery?.kind === "pull_request") {
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p>
          Draft pull request{" "}
          <a
            href={delivery.url}
            className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
          >
            #{delivery.number}
          </a>{" "}
          opened from <span className="font-mono text-xs">{delivery.branch}</span>.
        </p>
        {delivery.base_moved && (
          <p className="text-xs text-zinc-500">
            The default branch moved since the run; the PR is based on the validated commit.
          </p>
        )}
        {patch}
      </div>
    );
  }
  if (run.status === "creating_pr" || !delivery) {
    return (
      <Loading
        label={
          run.status === "creating_pr"
            ? "Opening the draft pull request"
            : "Approved, preparing delivery"
        }
      />
    );
  }
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>Approved. No pull request was opened:</p>
      <p className="text-xs text-zinc-600 dark:text-zinc-400">{delivery.reason}</p>
      <div className="flex items-center gap-3">
        {patch}
        {delivery.code !== "not_a_github_repository" && (
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            Retry pull request
          </Button>
        )}
      </div>
      {retry.isError && <ErrorState title="Retry failed" error={retry.error} />}
    </div>
  );
}
