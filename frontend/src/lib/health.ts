/** Types and client for the API's `/health` readiness endpoint. */

export type CheckStatus = "ok" | "error";

export interface CheckResult {
  status: CheckStatus;
  latency_ms: number;
  error: string | null;
}

export interface HealthReport {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, CheckResult>;
}

export type SystemHealth =
  | { kind: "reported"; httpStatus: number; report: HealthReport }
  | { kind: "unreachable"; message: string };

function isHealthReport(value: unknown): value is HealthReport {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    (candidate.status === "ok" || candidate.status === "degraded") &&
    typeof candidate.version === "string" &&
    typeof candidate.checks === "object" &&
    candidate.checks !== null
  );
}

/**
 * Fetch `/health`. A 503 still carries a report (a dependency is down), so both 200
 * and 503 are "reported"; anything else, or a network failure, is "unreachable".
 */
export async function fetchSystemHealth(
  apiBaseUrl: string,
  fetchImpl: typeof fetch = fetch,
  timeoutMs = 3000,
): Promise<SystemHealth> {
  let response: Response;
  try {
    response = await fetchImpl(new URL("/health", apiBaseUrl), {
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return { kind: "unreachable", message: `API request failed: ${reason}` };
  }

  if (response.status !== 200 && response.status !== 503) {
    return { kind: "unreachable", message: `API returned HTTP ${response.status}` };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "unreachable", message: "API returned a non-JSON response" };
  }
  if (!isHealthReport(body)) {
    return { kind: "unreachable", message: "API returned an unexpected health payload" };
  }
  return { kind: "reported", httpStatus: response.status, report: body };
}
