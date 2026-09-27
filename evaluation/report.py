"""Markdown benchmark report (spec 13.3), generated only from stored results."""

from evaluation.metrics import CaseResultOut, EvalRunDetail, RateOut


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f} s"


def _num(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _name(run: EvalRunDetail) -> str:
    return run.label or str(run.id)[:8]


def _rate(rate: RateOut) -> str:
    return str(rate)


def _case_row(r: CaseResultOut) -> str:
    outcome = "resolved" if r.resolved else (r.failure_category or "unresolved")
    run = f"`{r.agent_run_id}`" if r.agent_run_id else "-"
    return (
        f"| {r.case_id} | {r.difficulty} | {r.repeat} | {outcome} | "
        f"{r.fail_to_pass_passed}/{r.fail_to_pass_total} | {r.pass_to_pass_regressions} | "
        f"{r.retries} | {r.steps} | {_ms(r.wall_clock_ms)} | {r.tokens:,} | "
        f"${r.cost_usd:.4f} | {run} |"
    )


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def render(run: EvalRunDetail, baseline: EvalRunDetail | None = None) -> str:
    budget = run.config.get("budget", {})
    prompts = ", ".join(f"{k} {v}" for k, v in sorted(run.prompt_versions.items()))
    lines = [
        f"# DevAgent benchmark: {run.dataset} v{run.dataset_version} ({_name(run)})",
        "",
        f"- Evaluation run: `{run.id}` ({run.status})",
        f"- Model: `{run.model}`",
        f"- Started: {run.started_at:%Y-%m-%d %H:%M UTC}" if run.started_at else "- Started: n/a",
        f"- Cases scored: {run.n} (repeats per case: {run.repeats})",
        f"- Budget: {', '.join(f'{k}={v}' for k, v in budget.items()) or 'defaults'}",
        f"- Dataset sha256: `{run.config.get('dataset_sha256', 'n/a')}`",
        f"- Sandbox image: `{run.config.get('sandbox_image', 'n/a')}`",
        f"- Prompt versions: {prompts or 'n/a'}",
    ]
    if run.skipped:
        lines.append(f"- Not run: {', '.join(run.skipped)} (no recorded cassette for this model)")
    if run.model == "scripted":
        lines += [
            "",
            "> This run uses the `scripted` model, which replays a recorded cassette. It",
            "> shows that the harness, scoring and report work end to end; it says nothing",
            "> about how well any real model solves issues.",
        ]
    lines += [
        "",
        "## Results",
        "",
        "Rates show the 95% Wilson score interval. With few cases the interval is wide,",
        "so small differences between runs are not meaningful.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Resolved (hidden tests pass, no regressions) | {_rate(run.resolved)} |",
        f"| Patch applied to a fresh checkout | {_rate(run.patch_applied)} |",
        f"| Reproduction test created | {_rate(run.reproduction_created)} |",
        f"| Regression-free (of applied patches) | {_rate(run.regression_free)} |",
        f"| Mean debug retries | {_num(run.mean_retries, 2)} |",
        f"| Median steps | {_num(run.median_steps)} |",
        f"| Median wall clock | {_ms(run.median_wall_clock_ms)} |",
        f"| Median tokens | {_num(run.median_tokens, 0)} |",
        f"| Mean cost per case | {_money(run.mean_cost_usd)} |",
        f"| Total cost | ${run.total_cost_usd:.4f} |",
        "",
        "## By difficulty",
        "",
        "| Difficulty | Resolved |",
        "| --- | --- |",
        *(f"| {d} | {_rate(rate)} |" for d, rate in run.by_difficulty.items()),
        "",
        "## Failure taxonomy",
        "",
    ]
    if run.failures:
        lines += ["| Category | Cases |", "| --- | --- |"]
        lines += [f"| {category} | {count} |" for category, count in run.failures.items()]
    else:
        lines.append("No failures.")
    if baseline is not None:
        lines += [
            "",
            f"## Comparison with {_name(baseline)}",
            "",
            f"| Metric | {_name(run)} | {_name(baseline)} |",
            "| --- | --- | --- |",
            f"| Resolved | {_rate(run.resolved)} | {_rate(baseline.resolved)} |",
            f"| Patch applied | {_rate(run.patch_applied)} | {_rate(baseline.patch_applied)} |",
            f"| Mean debug retries | {_num(run.mean_retries, 2)} "
            f"| {_num(baseline.mean_retries, 2)} |",
            f"| Median steps | {_num(run.median_steps)} | {_num(baseline.median_steps)} |",
            f"| Median tokens | {_num(run.median_tokens, 0)} | {_num(baseline.median_tokens, 0)} |",
            f"| Total cost | ${run.total_cost_usd:.4f} | ${baseline.total_cost_usd:.4f} |",
            "",
            f"Budget of {_name(baseline)}: "
            + ", ".join(f"{k}={v}" for k, v in baseline.config.get("budget", {}).items()),
        ]
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Difficulty | Repeat | Outcome | F2P | P2P regressions | Retries | Steps "
        "| Wall clock | Tokens | Cost | Agent run |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *(_case_row(r) for r in run.results),
        "",
        "Each agent run's full trace (steps, tool calls, model calls, test runs) is at",
        "`/runs/<agent run id>` in the dashboard.",
        "",
    ]
    return "\n".join(lines)
