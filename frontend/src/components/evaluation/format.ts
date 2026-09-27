import type { FailureCategory, Rate } from "@/lib/api";

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : `${Math.round(value * 100)}%`;
}

/** "50% (1/2)" plus the 95% Wilson interval, or "n/a" when nothing was scored. */
export function rateLabel(rate: Rate): { value: string; hint: string } {
  if (rate.n === 0) return { value: "n/a", hint: "no scored cases" };
  return {
    value: `${percent(rate.value)} (${rate.k}/${rate.n})`,
    hint: `95% CI ${percent(rate.ci_low)} to ${percent(rate.ci_high)}`,
  };
}

export const FAILURE_LABELS: Record<FailureCategory, string> = {
  environment: "Environment",
  localization: "Localization",
  reproduction: "Reproduction",
  incorrect_fix: "Incorrect fix",
  regression: "Regression",
  invalid_patch: "Invalid patch",
  budget_exceeded: "Budget exceeded",
  timeout: "Timeout",
  infra: "Infrastructure",
};

export function runName(run: { label: string | null; id: string }): string {
  return run.label ?? run.id.slice(0, 8);
}
