from pathlib import Path

import pytest

from agent.analysis.analyzer import StaticRepositoryAnalyzer

SAMPLES = Path(__file__).parents[2] / "sample_repos"
PYTEST = ["python", "-m", "pytest"]


@pytest.mark.parametrize(
    ("repo", "package_manager", "install"),
    [
        ("textchunk", "pip", [["python", "-m", "pip", "install", "-e", ".[test]"]]),
        ("tzconvert", "pip", [["python", "-m", "pip", "install", "-r", "requirements.txt"]]),
        ("tagstore", "pip", [["python", "-m", "pip", "install", "-e", ".[test]"]]),
        ("kvconfig", "pip", [["python", "-m", "pip", "install", "-e", ".[dev]"]]),
        ("wallet", "pip", [["python", "-m", "pip", "install", "-r", "requirements.txt"]]),
        ("slugger", "pip", [["python", "-m", "pip", "install", "-e", ".[tests]"]]),
        ("notebook", "pip", [["python", "-m", "pip", "install", "-r", "requirements.txt"]]),
    ],
)
def test_every_sample_repo_is_detected_as_pytest(
    repo: str, package_manager: str, install: list[list[str]]
) -> None:
    profile = StaticRepositoryAnalyzer().analyze(SAMPLES / repo)
    assert profile.primary_language == "python"
    assert profile.supported, profile.unsupported_reason
    assert profile.test_framework == "pytest"
    assert profile.test_command == PYTEST
    assert profile.package_manager == package_manager
    assert profile.install_commands == install
    assert profile.test_directories == ["tests"]
    assert profile.evidence


def test_kvconfig_lint_format_and_typecheck_commands() -> None:
    profile = StaticRepositoryAnalyzer().analyze(SAMPLES / "kvconfig")
    assert profile.lint_command == ["ruff", "check", "."]
    assert profile.format_check_command == ["ruff", "format", "--check", "."]
    assert profile.typecheck_command == ["python", "-m", "mypy", "."]


def test_unconfigured_tools_are_not_invented() -> None:
    profile = StaticRepositoryAnalyzer().analyze(SAMPLES / "wallet")
    assert profile.lint_command is None
    assert profile.typecheck_command is None


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_poetry_django_project(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.poetry]\nname="x"\n[tool.poetry.dependencies]\n'
            'python="^3.11"\ndjango="^5"\n[tool.poetry.group.dev.dependencies]\npytest="*"\n',
            "poetry.lock": "",
            "manage.py": "",
            "app/__init__.py": "",
            "app/tests/test_views.py": "def test_x(): pass\n",
        },
    )
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.package_manager == "poetry"
    assert profile.framework == "django"
    assert profile.test_framework == "pytest"
    assert "manage.py" in profile.entry_points


def test_uv_project_with_scripts(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi"]\n'
            '[project.scripts]\nx = "x.cli:main"\n[dependency-groups]\ndev=["pytest"]\n',
            "uv.lock": "",
            "x/__init__.py": "",
            "tests/test_a.py": "",
        },
    )
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.package_manager == "uv"
    assert profile.framework == "fastapi"
    assert profile.entry_points == ["x = x.cli:main"]


def test_unittest_only_repo_is_reported_unsupported(tmp_path: Path) -> None:
    root = _write(tmp_path, {"m.py": "", "test_m.py": "import unittest\n"})
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.test_framework == "unittest"
    assert not profile.supported
    assert profile.unsupported_reason == "v1 supports pytest-based Python projects only"


def test_pytest_added_when_repo_does_not_declare_it(tmp_path: Path) -> None:
    root = _write(tmp_path, {"requirements.txt": "requests\n", "tests/conftest.py": "", "a.py": ""})
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.install_commands[-1] == ["python", "-m", "pip", "install", "pytest"]


def test_javascript_repo_has_no_adapter_yet(tmp_path: Path) -> None:
    root = _write(tmp_path, {"package.json": "{}", "index.js": "", "lib/a.js": ""})
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.primary_language == "javascript"
    assert not profile.supported
    assert profile.unsupported_reason == "no language adapter for this repository"


def test_skips_vendored_dirs_and_symlinks(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {"a.py": "", "node_modules/x/index.js": "", ".venv/lib/y.py": "", "tests/test_a.py": ""},
    )
    (root / "link.py").symlink_to("/etc/passwd")
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.languages == {"python": 2}


def test_malformed_config_does_not_crash(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {"pyproject.toml": "[[[not toml", "setup.cfg": "no section header", "a.py": ""},
    )
    profile = StaticRepositoryAnalyzer().analyze(root)
    assert profile.primary_language == "python"


def test_empty_repo(tmp_path: Path) -> None:
    profile = StaticRepositoryAnalyzer().analyze(tmp_path)
    assert profile.primary_language is None
    assert profile.unsupported_reason == "no source files found"
