"""Assemble and execute one agent run on the worker.

This is the composition root for the agent: it reads the run's config snapshot, builds
the model client (metered against the run budget), the tool registry with its
database sink, the sandbox and the repository source, and runs the orchestrator.

While the run executes, a watcher polls the run's status. If the run is cancelled (or
otherwise leaves the worker's control), it kills the run's sandbox containers and
cancels the orchestrator, so a long test command does not outlive the cancel.
"""

import asyncio
import shutil
from collections.abc import Coroutine
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from agent.components.runtime import ComponentRuntime, ModelSettings
from agent.orchestrator import AgentConfig, IssueSpec, StateMachineOrchestrator
from agent.ports import RunCancelledError, RunUpdate
from backend.config import Settings
from backend.llm_factory import build_llm_client, load_pricing
from backend.services.llm_log import DbLlmCallSink
from backend.services.recorder import DbRunRecorder
from backend.services.sources import source_for
from backend.services.test_log import DbTestRunSink
from backend.services.tool_log import DbToolCallSink
from core.run_status import RunStatus, is_terminal
from database.models import AgentRun, Repository
from llm.budget import BudgetLimits, BudgetTracker
from llm.metered import MeteredLLMClient
from llm.prompting import PromptLibrary
from sandbox.docker_sandbox import DockerSandbox, RunWorkspace, SandboxError
from tools.defaults import default_registry
from workspace.clone import CloneConfig
from workspace.limits import RepoLimits

logger = structlog.get_logger(__name__)

WATCH_INTERVAL_SECONDS = 1.0
ARTIFACTS_DIRNAME = "_artifacts"


def budget_limits(config: dict[str, Any]) -> BudgetLimits:
    raw = config.get("budget", {})
    defaults = BudgetLimits()
    return BudgetLimits(
        max_steps=int(raw.get("max_steps", defaults.max_steps)),
        max_fix_attempts=int(raw.get("max_fix_attempts", defaults.max_fix_attempts)),
        max_tokens=int(raw.get("max_tokens", defaults.max_tokens)),
        max_cost_usd=Decimal(str(raw.get("max_cost_usd", defaults.max_cost_usd))),
        command_timeout_seconds=int(
            raw.get("command_timeout_seconds", defaults.command_timeout_seconds)
        ),
        wall_clock_seconds=int(raw.get("wall_clock_seconds", defaults.wall_clock_seconds)),
    )


async def run_agent(
    *,
    run_id: UUID,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    recorder: DbRunRecorder,
    sandbox: DockerSandbox | None,
) -> RunStatus:
    """Execute an agent run. Raises ``RunCancelledError`` if it was cancelled."""
    async with session_factory() as session:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).options(selectinload(AgentRun.issue))
        )
        if run is None:
            raise RunCancelledError(f"run {run_id} no longer exists")
        repo = await session.get(Repository, run.repository_id)
        if repo is None:
            raise RunCancelledError(f"repository of run {run_id} no longer exists")
        config = dict(run.config)
        issue = IssueSpec(
            title=run.issue.title, body=run.issue.body, number=run.issue.number, url=run.issue.url
        )

    if sandbox is None:
        raise SandboxError("sandbox_unavailable", "Docker or the sandbox image is not available")

    pricing = load_pricing(settings)
    model = config.get("model") or settings.llm_model or ""
    inner = build_llm_client(settings, pricing, model)
    budget = BudgetTracker(budget_limits(config))
    limits = RepoLimits(max_bytes=settings.max_repo_bytes, max_files=settings.max_repo_files)
    workspace_root = Path(settings.workspace_root)
    workspace = RunWorkspace.under(workspace_root, str(run_id))
    artifacts = workspace_root / ARTIFACTS_DIRNAME / str(run_id)

    model_settings = ModelSettings(
        model=model,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    await recorder.update_run(
        RunUpdate(
            config={
                "model": model,
                "provider": inner.provider,
                "temperature": model_settings.temperature,
                "max_output_tokens": model_settings.max_output_tokens,
            }
        )
    )
    client = MeteredLLMClient(
        inner, pricing, budget, str(run_id), DbLlmCallSink(session_factory, run_id, recorder)
    )
    registry = default_registry(DbToolCallSink(session_factory, run_id, artifacts, recorder))
    runtime = ComponentRuntime(
        client, model_settings, PromptLibrary(Path(settings.prompts_path)), registry
    )
    orchestrator = StateMachineOrchestrator(
        config=AgentConfig(
            run_url=f"{settings.public_web_url.rstrip('/')}/runs/{run_id}",
            repo_limits=limits,
            localizer_rounds=settings.agent_localizer_rounds,
            reproducer_rounds=settings.agent_reproducer_rounds,
            editor_rounds=settings.agent_editor_rounds,
        ),
        issue=issue,
        source=source_for(
            repo,
            samples_root=Path(settings.sample_repos_path),
            limits=limits,
            clone=CloneConfig(limits=limits, timeout_seconds=settings.clone_timeout_seconds),
        ),
        workspace=workspace,
        sandbox=sandbox,
        runtime=runtime,
        budget=budget,
        recorder=recorder,
        test_sink=DbTestRunSink(session_factory, run_id, recorder),
    )
    try:
        return await _run_with_watch(orchestrator.run(), run_id, session_factory, sandbox)
    finally:
        await sandbox.kill_run(str(run_id))
        if not settings.keep_workspaces:
            await asyncio.to_thread(shutil.rmtree, workspace.root, True)


async def _run_with_watch(
    work: Coroutine[Any, Any, RunStatus],
    run_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
    sandbox: DockerSandbox,
) -> RunStatus:
    task: asyncio.Task[RunStatus] = asyncio.ensure_future(work)
    watcher = asyncio.ensure_future(_watch(run_id, session_factory))
    try:
        done, _pending = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        stopped = watcher.result()
        logger.info("run_stopped_externally", run_id=str(run_id), status=stopped.value)
        killed = await sandbox.kill_run(str(run_id))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise RunCancelledError(f"run is {stopped.value} (killed {killed} containers)")
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _watch(run_id: UUID, session_factory: async_sessionmaker[AsyncSession]) -> RunStatus:
    """Return once the run is in a final state set by someone else (e.g. cancelled)."""
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        async with session_factory() as session:
            status = await session.scalar(select(AgentRun.status).where(AgentRun.id == run_id))
        if status is None:
            return RunStatus.CANCELLED
        if is_terminal(status):
            return status
