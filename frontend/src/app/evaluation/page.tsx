import type { Metadata } from "next";

import { EvaluationView } from "@/components/evaluation/evaluation-view";

export const metadata: Metadata = { title: "Evaluation" };

export default function EvaluationPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Evaluation</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Benchmark results from the evaluation harness. Every number comes from scored runs stored
          in the database.
        </p>
      </div>
      <EvaluationView />
    </div>
  );
}
