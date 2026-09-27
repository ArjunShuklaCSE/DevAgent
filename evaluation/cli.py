"""``devagent eval``: validate datasets, run benchmarks and render reports.

Run it where the worker runs (it needs Postgres, Redis and the sandbox), e.g.
``docker compose exec worker devagent eval run --model scripted``.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from redis.asyncio import Redis

from backend.config import Settings, get_settings
from backend.event_bus import RedisEventBus
from backend.logging_setup import configure_logging
from backend.sandbox_factory import build_sandbox
from backend.schemas import RunBudget
from backend.services.sources import LocalSampleSource
from database.engine import create_engine, create_session_factory
from evaluation.dataset import Dataset, DatasetError, load_dataset
from evaluation.harness import Harness, HarnessOptions
from evaluation.metrics import list_runs, load_detail
from evaluation.report import render
from evaluation.scoring import Scorer
from workspace.limits import RepoLimits

DEFAULT_DATASET = Path("evaluation/datasets/starter.yaml")
DEFAULT_REPORTS = Path("evaluation/reports")


def _print(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------- validate


async def _validate(settings: Settings, dataset: Dataset, case_ids: Sequence[str]) -> int:
    """Check each case: hidden F2P tests fail on the base and pass with the gold patch."""
    sandbox = build_sandbox(settings)
    scorer = Scorer(sandbox, Path(settings.workspace_root))
    limits = RepoLimits(max_bytes=settings.max_repo_bytes, max_files=settings.max_repo_files)
    problems = 0
    try:
        for case in [dataset.case(c) for c in case_ids] or dataset.cases:
            if case.sample is None or case.gold_patch is None:
                _print(f"SKIP {case.id}: needs a sample repository and a gold patch")
                continue
            source = LocalSampleSource(Path(settings.sample_repos_path), case.sample, limits)
            base = await scorer.score(dataset, case, source, None)
            gold_diff = dataset.file(case.gold_patch).read_text("utf-8")
            gold = await scorer.score(dataset, case, source, gold_diff)
            base_ok = (
                base.error is None
                and base.fail_to_pass_passed == 0
                and base.pass_to_pass_regressions == 0
            )
            gold_ok = gold.resolved
            status = "OK  " if base_ok and gold_ok else "FAIL"
            problems += status == "FAIL"
            _print(
                f"{status} {case.id}: base F2P {base.fail_to_pass_passed}/"
                f"{len(case.fail_to_pass)} passing, P2P regressions "
                f"{base.pass_to_pass_regressions}; gold F2P {gold.fail_to_pass_passed}/"
                f"{len(case.fail_to_pass)}, P2P regressions {gold.pass_to_pass_regressions}"
                + (f"; error: {base.error or gold.error}" if base.error or gold.error else "")
            )
    finally:
        sandbox.close()
    return 1 if problems else 0


# ---------------------------------------------------------------------- run / report


async def _run(settings: Settings, dataset: Dataset, args: argparse.Namespace) -> int:
    budget = RunBudget(
        **({"max_fix_attempts": args.max_fix_attempts} if args.max_fix_attempts is not None else {})
    )
    options = HarnessOptions(
        model=args.model,
        label=args.label,
        repeats=args.repeats,
        concurrency=args.concurrency,
        case_ids=args.case or (),
        budget=budget,
    )
    engine = create_engine(settings.database_url.get_secret_value())
    redis: Redis = Redis.from_url(settings.redis_url.get_secret_value())
    sandbox = build_sandbox(settings)
    try:
        sessions = create_session_factory(engine)
        harness = Harness(
            settings=settings,
            session_factory=sessions,
            bus=RedisEventBus(redis),
            sandbox=sandbox,
        )
        eval_run_id = await harness.run(dataset, options)
        async with sessions() as session:
            detail = await load_detail(session, eval_run_id)
        if detail is None:
            return 1
        report = render(detail)
        out = args.report_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{detail.label or 'run'}.md"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report, "utf-8")
            _print(f"report: {out}")
        except OSError as exc:
            _print(f"could not write the report file ({exc}); printing it instead\n")
            _print(report)
        _print(f"eval run: {eval_run_id}")
        _print(f"resolved: {detail.resolved}")
        return 0
    finally:
        sandbox.close()
        await redis.aclose()
        await engine.dispose()


async def _report(settings: Settings, args: argparse.Namespace) -> int:
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        async with create_session_factory(engine)() as session:
            if args.run is None:
                for run in await list_runs(session):
                    _print(
                        f"{run.id}  {run.started_at:%Y-%m-%d %H:%M}  {run.dataset} "
                        f"{run.model:<20} {run.label or '-':<20} resolved {run.resolved}"
                    )
                return 0
            detail = await load_detail(session, args.run)
            baseline = await load_detail(session, args.compare) if args.compare else None
        if detail is None or (args.compare and baseline is None):
            _print("evaluation run not found")
            return 1
        report = render(detail, baseline)
        if args.out:
            args.out.write_text(report, "utf-8")
            _print(f"report: {args.out}")
        else:
            _print(report)
        return 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------- entry point


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devagent")
    top = parser.add_subparsers(dest="group", required=True)
    commands = top.add_parser("eval", help="benchmark the agent").add_subparsers(
        dest="command", required=True
    )

    validate = commands.add_parser("validate", help="check a dataset (and its gold patches)")
    validate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    validate.add_argument("--case", action="append", help="only this case (repeatable)")
    validate.add_argument(
        "--gold",
        action="store_true",
        help="also run hidden tests on the base and the gold patch in the sandbox",
    )

    run = commands.add_parser("run", help="run the agent on every case and score it")
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--model", required=True, help="model name from the pricing file")
    run.add_argument("--label", help="name for this run in reports, e.g. no-debug-loop")
    run.add_argument("--case", action="append", help="only this case (repeatable)")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument(
        "--max-fix-attempts", type=int, help="override the budget (0 disables the debug loop)"
    )
    run.add_argument("--report-dir", type=Path, default=DEFAULT_REPORTS)

    report = commands.add_parser("report", help="render a stored run (or list runs)")
    report.add_argument("run", type=UUID, nargs="?", help="evaluation run id; omit to list")
    report.add_argument("--compare", type=UUID, help="a second run to compare against")
    report.add_argument("--out", type=Path, help="write to this file instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    try:
        if args.command == "report":
            return asyncio.run(_report(settings, args))
        dataset = load_dataset(args.dataset)
        if args.command == "validate":
            _print(f"{dataset.name} v{dataset.version}: {len(dataset.cases)} cases, format OK")
            if not args.gold:
                return 0
            return asyncio.run(_validate(settings, dataset, args.case or ()))
        if args.repeats < 1 or args.concurrency < 1:
            _print("--repeats and --concurrency must be at least 1")
            return 2
        return asyncio.run(_run(settings, dataset, args))
    except DatasetError as exc:
        _print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
