"""Which workspace paths an agent may change, and which changes a reviewer must see.

- ``blocked``: never writable (``.git/``).
- ``protected``: CI configuration and lockfiles. Writable only when the approved plan
  names the path; the change is always flagged for the reviewer (spec 5, path safety).
- ``sensitive``: writable, but flagged on the approval screen: dependency manifests,
  build and test configuration.
- ``normal``: everything else.
"""

from fnmatch import fnmatchcase
from typing import Literal

PathClass = Literal["blocked", "protected", "sensitive", "normal"]

_BLOCKED = (".git", ".git/*")
_PROTECTED = (
    ".github/workflows/*",
    ".github/actions/*",
    ".gitlab-ci.yml",
    ".circleci/*",
    ".travis.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
    "bitbucket-pipelines.yml",
    "*poetry.lock",
    "*uv.lock",
    "*Pipfile.lock",
    "*pdm.lock",
    "*package-lock.json",
    "*pnpm-lock.yaml",
    "*yarn.lock",
)
_SENSITIVE = (
    ".github/*",
    "*requirements*.txt",
    "*requirements*.in",
    "*pyproject.toml",
    "*setup.py",
    "*setup.cfg",
    "*Pipfile",
    "*package.json",
    "*conftest.py",
    "*pytest.ini",
    "*tox.ini",
    "*noxfile.py",
    "*Makefile",
    "*Dockerfile*",
    "*docker-compose*.yml",
    "*compose.yaml",
    ".pre-commit-config.yaml",
    "*.env*",
    "*.sh",
)


def classify_path(relative_path: str) -> PathClass:
    """Classify a workspace-relative POSIX path."""
    path = relative_path.removeprefix("./")
    if _matches(path, _BLOCKED):
        return "blocked"
    if _matches(path, _PROTECTED):
        return "protected"
    if _matches(path, _SENSITIVE):
        return "sensitive"
    return "normal"


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)
