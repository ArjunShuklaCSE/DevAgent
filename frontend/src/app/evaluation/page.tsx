import type { Metadata } from "next";

import { Card, EmptyState } from "@/components/ui";

export const metadata: Metadata = { title: "Evaluation" };

export default function EvaluationPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Evaluation</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Benchmark results from the evaluation harness: resolve rate, cost and failure categories.
        </p>
      </div>
      <Card>
        <EmptyState title="No data yet">
          No evaluation has been run. Results appear here after{" "}
          <code className="font-mono">make eval</code>.
        </EmptyState>
      </Card>
    </div>
  );
}
