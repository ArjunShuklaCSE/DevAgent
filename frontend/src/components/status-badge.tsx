import type { RunStatus } from "@/lib/api";
import { humanize } from "@/lib/format";

import { Badge } from "./ui";

const TONE: Partial<Record<RunStatus, "info" | "success" | "warning" | "danger" | "neutral">> = {
  queued: "neutral",
  awaiting_approval: "warning",
  approved: "success",
  pr_created: "success",
  rejected: "neutral",
  failed: "danger",
  budget_exceeded: "danger",
  timed_out: "danger",
  cancelled: "neutral",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  const tone = TONE[status] ?? "info";
  return (
    <Badge tone={tone}>
      {tone === "info" && (
        <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-current" />
      )}
      {humanize(status)}
    </Badge>
  );
}
