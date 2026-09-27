"""Where a run's repository comes from: a GitHub clone or a bundled sample repository."""

import re
from pathlib import Path

from agent.ports import SourceCheckout
from database.models import Repository, RepositorySource
from workspace.clone import CloneConfig, CloneError, GitCloner, copy_local_repository
from workspace.limits import RepoLimits

SAMPLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class GitHubSource:
    def __init__(self, cloner: GitCloner, url: str, ref: str | None = None) -> None:
        self._cloner = cloner
        self._url = url
        self._ref = ref
        self.description = url

    async def checkout(self, dest: Path) -> SourceCheckout:
        result = await self._cloner.clone(self._url, dest, self._ref)
        return SourceCheckout(result.commit_sha, result.stats.files, result.stats.bytes)


class LocalSampleSource:
    """A sample repository from ``sample_repos/`` (copied and committed as the base)."""

    def __init__(self, samples_root: Path, name: str, limits: RepoLimits) -> None:
        if not SAMPLE_NAME.match(name):
            raise CloneError("unknown_sample", f"invalid sample repository name {name!r}")
        self._path = samples_root / name
        self._limits = limits
        self.description = f"sample repository '{name}'"

    async def checkout(self, dest: Path) -> SourceCheckout:
        if not self._path.is_dir():
            raise CloneError("unknown_sample", f"no sample repository at {self._path}")
        result = await copy_local_repository(self._path, dest, self._limits)
        return SourceCheckout(result.commit_sha, result.stats.files, result.stats.bytes)


def list_samples(samples_root: Path) -> list[str]:
    if not samples_root.is_dir():
        return []
    return sorted(
        p.name for p in samples_root.iterdir() if p.is_dir() and SAMPLE_NAME.match(p.name)
    )


def source_for(
    repo: Repository, *, samples_root: Path, limits: RepoLimits, clone: CloneConfig
) -> GitHubSource | LocalSampleSource:
    if repo.source is RepositorySource.LOCAL:
        return LocalSampleSource(samples_root, repo.name, limits)
    return GitHubSource(GitCloner(clone), repo.clone_url)
