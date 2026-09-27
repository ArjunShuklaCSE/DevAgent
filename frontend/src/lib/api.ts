/**
 * Typed client for the DevAgent API. The browser calls same-origin `/api/v1/*`, which
 * the Next.js server forwards to the API service (see `app/api/v1/[...path]/route.ts`).
 */

export type RunStatus =
  | "queued"
  | "cloning"
  | "analyzing_repo"
  | "analyzing_issue"
  | "localizing"
  | "reproducing"
  | "planning"
  | "editing"
  | "testing"
  | "debugging"
  | "validating"
  | "awaiting_approval"
  | "approved"
  | "creating_pr"
  | "pr_created"
  | "rejected"
  | "failed"
  | "budget_exceeded"
  | "cancelled"
  | "timed_out";

export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "pr_created",
  "rejected",
  "failed",
  "budget_exceeded",
  "cancelled",
  "timed_out",
]);

export interface Repository {
  id: string;
  source: "github" | "local";
  owner: string;
  name: string;
  clone_url?: string;
  default_branch?: string | null;
  created_at?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Budget {
  max_steps: number;
  max_fix_attempts: number;
  max_tokens: number;
  max_cost_usd: string;
  command_timeout_seconds: number;
  wall_clock_seconds: number;
}

export interface RunSummary {
  id: string;
  repository_id: string;
  repository: Repository;
  issue: { number: number | null; title: string };
  mode: "agent" | "dry_run";
  status: RunStatus;
  status_reason: string | null;
  step_count: number;
  fix_attempts: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface InjectionFlag {
  pattern: string;
  label: string;
  source: string;
  line: number;
  excerpt: string;
}

export interface Run extends Omit<RunSummary, "issue"> {
  issue: { id: string; number: number | null; title: string; body: string; source: string };
  config: Record<string, unknown> & { budget?: Budget; model?: string; provider?: string };
  base_commit_sha: string | null;
  sandbox_image_digest: string | null;
  prompt_versions: Record<string, string>;
  final_diff_sha256: string | null;
  injection_flags: InjectionFlag[];
  result: RunResult;
  last_event_seq: number;
}

export interface CheckResult {
  name: "reproduction" | "test_suite" | "lint" | "format" | "typecheck";
  status: "passed" | "failed" | "pre_existing" | "not_configured" | "error";
  command: string[] | null;
  detail: string;
}

export interface ReviewFlag {
  kind: "sensitive_path" | "protected_path" | "new_dependency" | "network_call" | "deleted_test";
  path: string;
  detail: string;
}

export interface RunResult {
  issue_analysis?: {
    summary: string;
    expected_behavior: string;
    current_behavior: string;
    acceptance_criteria: string[];
    confidence: string;
  };
  plan?: {
    steps: string[];
    files_to_touch: string[];
    risks: string[];
    test_strategy: string;
    rationale: string;
  };
  reproduction?: { test_file: string; explanation: string; failing_before_fix: string[] };
  validation?: { checks: CheckResult[] };
  review_flags?: ReviewFlag[];
  pull_request?: { title: string; body: string; commit_message: string };
  failure?: { code: string; message: string; category: string };
  budget?: Record<string, unknown>;
}

export interface Step {
  id: string;
  sequence: number;
  state: RunStatus;
  status: "running" | "completed" | "failed" | "skipped";
  synthetic: boolean;
  summary: string | null;
  rationale: string | null;
  output: Record<string, unknown>;
  error: { code: string; message: string } | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface ToolCall {
  id: string;
  step_id: string | null;
  tool_name: string;
  capability: string;
  input: Record<string, unknown>;
  output_truncated: string | null;
  output_size_bytes: number;
  status: "ok" | "error" | "denied";
  error: { code: string; message: string } | null;
  duration_ms: number;
  created_at: string;
}

export interface LlmCall {
  id: string;
  step_id: string | null;
  provider: string;
  model: string;
  component: string;
  prompt_version: string;
  attempt: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  latency_ms: number;
  status: "ok" | "error" | "denied";
  rationale: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
}

export interface TestRunRecord {
  id: string;
  step_id: string | null;
  kind: "reproduction" | "suite" | "validation" | "hidden";
  command: string[];
  exit_code: number | null;
  timed_out: boolean;
  duration_ms: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  created_at: string;
  results: {
    node_id: string;
    outcome: string;
    duration_ms: number | null;
    message: string | null;
  }[];
}

export interface RunDiff {
  run_id: string;
  diff: string | null;
  sha256: string | null;
  review_flags: ReviewFlag[];
  validation: { checks: CheckResult[] } | null;
}

export interface RunCreate {
  repository_id: string;
  issue: { title: string; body: string; number?: number | null };
  model?: string | null;
  budget?: Partial<Budget>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Parse the API's error envelope `{ error: { code, message, details } }`. */
export async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    };
    if (body.error?.code) {
      return new ApiError(
        response.status,
        body.error.code,
        body.error.message ?? body.error.code,
        body.error.details ?? {},
      );
    }
  } catch {
    // not JSON; fall through
  }
  return new ApiError(response.status, "http_error", `Request failed with HTTP ${response.status}`);
}

async function request<T>(
  path: string,
  init?: RequestInit,
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(`/api/v1${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch (error) {
    throw new ApiError(0, "network_error", error instanceof Error ? error.message : String(error));
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  repositories: () => request<Page<Repository>>("/repositories?limit=100"),
  samples: () => request<string[]>("/repositories/samples"),
  addRepository: (body: { url: string } | { sample: string }) =>
    post<Repository>("/repositories", body),
  runs: (status?: string) =>
    request<Page<RunSummary>>(
      `/runs?limit=100${status ? `&status=${encodeURIComponent(status)}` : ""}`,
    ),
  run: (id: string) => request<Run>(`/runs/${id}`),
  createRun: (body: RunCreate) => post<Run>("/runs", body),
  cancel: (id: string) =>
    post<Run>(`/runs/${id}/cancel`, { reason: "cancelled from the dashboard" }),
  steps: (id: string) => request<Step[]>(`/runs/${id}/steps`),
  toolCalls: (id: string) => request<ToolCall[]>(`/runs/${id}/tool-calls`),
  llmCalls: (id: string) => request<LlmCall[]>(`/runs/${id}/llm-calls`),
  testRuns: (id: string) => request<TestRunRecord[]>(`/runs/${id}/test-runs`),
  diff: (id: string) => request<RunDiff>(`/runs/${id}/diff`),
  approve: (id: string, diffSha256: string, comment: string) =>
    post<Run>(`/runs/${id}/approve`, { diff_sha256: diffSha256, comment: comment || null }),
  reject: (id: string, comment: string) =>
    post<Run>(`/runs/${id}/reject`, { comment: comment || null }),
};

export { request as _request };
