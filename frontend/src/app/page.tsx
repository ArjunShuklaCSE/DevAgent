import { connection } from "next/server";

import { SystemStatus } from "@/components/system-status";
import { fetchSystemHealth } from "@/lib/health";

const API_URL = process.env.DEVAGENT_API_INTERNAL_URL ?? "http://localhost:8000";

export default async function Home() {
  await connection(); // health is per-request; never prerender it
  const health = await fetchSystemHealth(API_URL);

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-16">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">DevAgent</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Autonomous GitHub issue solver. The run dashboard arrives in Phase 7.
        </p>
      </div>
      <SystemStatus health={health} />
    </main>
  );
}
