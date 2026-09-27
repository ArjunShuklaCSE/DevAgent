"use client";

import { useState } from "react";

import { RunsTable } from "@/components/recent-runs";
import { Card, inputClass } from "@/components/ui";
import { humanize } from "@/lib/format";

const FILTERS = [
  "",
  "queued",
  "awaiting_approval",
  "approved",
  "pr_created",
  "rejected",
  "failed",
  "budget_exceeded",
  "timed_out",
  "cancelled",
];

export default function RunsPage() {
  const [status, setStatus] = useState("");
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold tracking-tight">Runs</h1>
      <Card
        title="History"
        actions={
          <select
            aria-label="Filter by status"
            className={`${inputClass} w-48 py-1 text-xs`}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            {FILTERS.map((value) => (
              <option key={value} value={value}>
                {value ? humanize(value) : "All statuses"}
              </option>
            ))}
          </select>
        }
      >
        <RunsTable status={status || undefined} />
      </Card>
    </div>
  );
}
