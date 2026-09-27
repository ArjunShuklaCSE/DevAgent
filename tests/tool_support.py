"""A small git workspace for tool tests, plus a fake sandbox."""

from collections.abc import Sequence
from pathlib import Path

from sandbox.docker_sandbox import CommandResult, RunWorkspace, TestRun
from sandbox.junit import TestCaseResult, TestReport
from sandbox.policy import Profile
from tools.base import ToolContext
from tools.git import git

FILES = {
    "README.md": "# demo\n",
    ".gitignore": "ignored/\n*.log\n",
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "LIMIT = 10\n"
        "\n"
        "\n"
        "class Wallet:\n"
        "    def __init__(self, balance: int) -> None:\n"
        "        self.balance = balance\n"
        "\n"
        "    def withdraw(self, amount: int) -> int:\n"
        "        if amount > self.balance:\n"
        "            raise ValueError('insufficient')\n"
        "        self.balance -= amount\n"
        "        return self.balance\n"
        "\n"
        "\n"
        "def chunk(items: list[int], size: int) -> list[list[int]]:\n"
        "    return [items[i : i + size] for i in range(0, len(items) - size, size)]\n"
    ),
    "pkg/util.py": (
        "from pkg.core import Wallet, chunk\n\n\n"
        "def drain(w: Wallet) -> int:\n    return w.withdraw(amount=w.balance)\n"
    ),
    "tests/test_core.py": (
        "from pkg.core import chunk\n\n\n"
        "def test_chunk():\n    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]\n"
    ),
    "requirements.txt": "pytest\n",
    ".github/workflows/ci.yml": "on: push\n",
    "docs/deep/a/b/c/note.txt": "deep\n",
}


async def make_workspace(tmp_path: Path) -> tuple[RunWorkspace, str]:
    workspace = RunWorkspace.under(tmp_path, "run-1")
    repo = workspace.repo
    for name, content in FILES.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "secret.py").write_text("HIDDEN = 1\n")
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "lib" / "index.js").write_text("module.exports = 1\n")
    (repo / "debug.log").write_text("chunk in a log\n")
    await git(repo, ["init", "--quiet"])
    await git(repo, ["add", "--all"])
    identity = ["-c", "user.name=t", "-c", "user.email=t@localhost"]
    await git(repo, [*identity, "commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", "base"])
    sha = (await git(repo, ["rev-parse", "HEAD"])).strip()
    return workspace, sha


def context(
    workspace: RunWorkspace,
    base: str,
    sandbox: "FakeSandbox | None" = None,
    allowed: frozenset[str] = frozenset(),
) -> ToolContext:
    return ToolContext(
        run_id="run-1",
        workspace=workspace,
        base_commit=base,
        sandbox=sandbox,
        test_command=("python", "-m", "pytest"),
        allowed_protected_paths=allowed,
    )


class FakeSandbox:
    """Records calls; returns canned results (real sandbox tests live in integration)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run(
        self,
        workspace: RunWorkspace,
        argv: Sequence[str],
        *,
        profile: Profile = "run",
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        self.calls.append(("run", tuple(argv)))
        return CommandResult(
            argv=tuple(argv),
            profile=profile,
            exit_code=0,
            stdout="hello\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_seconds=0.2,
        )

    async def run_tests(
        self,
        workspace: RunWorkspace,
        test_command: Sequence[str],
        extra_args: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> TestRun:
        argv = (*test_command, *extra_args)
        self.calls.append(("tests", argv))
        result = CommandResult(
            argv=argv,
            profile="run",
            exit_code=1,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_seconds=1.5,
        )
        report = TestReport(
            total=2,
            passed=1,
            failed=1,
            errors=0,
            skipped=0,
            duration_seconds=0.1,
            cases=[
                TestCaseResult(
                    test_id="tests.test_core::test_ok", outcome="passed", duration_seconds=0.0
                ),
                TestCaseResult(
                    test_id="tests.test_core::test_chunk",
                    outcome="failed",
                    duration_seconds=0.1,
                    message="assert [[1, 2]] == [[1, 2], [3, 4]]",
                ),
            ],
        )
        return TestRun(result=result, report=report)
