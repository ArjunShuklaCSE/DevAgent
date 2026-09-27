import Link from "next/link";
import { connection } from "next/server";

import { NewRunForm } from "@/components/new-run-form";
import { RunsTable } from "@/components/recent-runs";
import { SystemStatus } from "@/components/system-status";
import { Card } from "@/components/ui";
import { fetchSystemHealth } from "@/lib/health";

const API_URL = process.env.DEVAGENT_API_INTERNAL_URL ?? "http://localhost:8000";

export default async function Home() {
  await connection(); // health is per-request; never prerender it
  const health = await fetchSystemHealth(API_URL);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Fix an issue</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          DevAgent reproduces the bug with a failing test, fixes it in a sandbox, validates the
          change and waits for your approval.
        </p>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_20rem]">
        <NewRunForm />
        <SystemStatus health={health} />
      </div>
      <Card
        title="Recent runs"
        actions={
          <Link
            href="/runs"
            className="text-xs text-indigo-600 hover:underline dark:text-indigo-400"
          >
            All runs
          </Link>
        }
      >
        <RunsTable limit={5} />
      </Card>
    </div>
  );
}
