"""Language adapters. Adding a language (v2: JS/TS) means adding an adapter here."""

import configparser
import re
from typing import Any, Literal, Protocol

from agent.analysis.model import Command, Evidence, RepoProfile
from agent.analysis.scan import RepoTree

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_FRAMEWORKS = ("django", "fastapi", "flask", "starlette", "aiohttp", "tornado", "pyramid")


class LanguageAdapter(Protocol):
    name: str
    languages: frozenset[str]

    def applies(self, tree: RepoTree) -> bool: ...

    def analyze(self, tree: RepoTree) -> RepoProfile: ...


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith(("-", "git+", "http")):
            continue
        match = _REQ_NAME.match(stripped)
        if match:
            names.add(_normalize(match.group(1)))
    return names


def _get(data: dict[str, Any], *keys: str) -> Any:  # noqa: ANN401 - untyped TOML tree
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


class PythonAdapter:
    name = "python"
    languages = frozenset({"python"})

    def applies(self, tree: RepoTree) -> bool:
        return tree.language_counts.get("python", 0) > 0

    def analyze(self, tree: RepoTree) -> RepoProfile:  # noqa: PLR0912, PLR0915
        evidence: list[Evidence] = []
        pyproject = tree.read_toml("pyproject.toml") if tree.exists("pyproject.toml") else {}
        setup_cfg = self._setup_cfg(tree)

        dependency_files = [
            f
            for f in tree.files
            if "/" not in f
            and (
                re.fullmatch(r"requirements([-_.][\w.-]+)?\.(txt|in)", f)
                or f
                in {
                    "pyproject.toml",
                    "setup.py",
                    "setup.cfg",
                    "Pipfile",
                    "Pipfile.lock",
                    "poetry.lock",
                    "uv.lock",
                    "environment.yml",
                }
            )
        ]
        dependency_files += [
            f for f in tree.files if f.startswith("requirements/") and f.endswith(".txt")
        ]

        deps = self._dependencies(tree, pyproject, setup_cfg, dependency_files)

        # ---- package manager
        package_manager: str
        if tree.exists("uv.lock"):
            package_manager = "uv"
            evidence.append(Evidence(claim="uv lockfile", source="uv.lock"))
        elif tree.exists("poetry.lock") or _get(pyproject, "tool", "poetry") is not None:
            package_manager = "poetry"
            evidence.append(Evidence(claim="poetry project", source="pyproject.toml"))
        elif tree.exists("Pipfile"):
            package_manager = "pipenv"
            evidence.append(Evidence(claim="Pipfile present", source="Pipfile"))
        else:
            package_manager = "pip"

        # ---- install commands (run in the sandbox's network-enabled install step)
        install: list[Command] = []
        req_files = [f for f in dependency_files if f.endswith(".txt") and "requirements" in f]
        for req in sorted(req_files, key=lambda f: (f != "requirements.txt", f)):
            install.append(["python", "-m", "pip", "install", "-r", req])
        installable = (
            _get(pyproject, "build-system") is not None
            or _get(pyproject, "project") is not None
            or tree.exists("setup.py")
            or tree.exists("setup.cfg")
        )
        if installable:
            extras = self._test_extras(pyproject, setup_cfg)
            target = f".[{','.join(extras)}]" if extras else "."
            install.append(["python", "-m", "pip", "install", "-e", target])
        if package_manager == "poetry" and not installable:
            install.append(["python", "-m", "pip", "install", "-e", "."])

        # ---- test framework
        test_framework: str | None = None
        pytest_sources = [
            ("pytest.ini", tree.exists("pytest.ini")),
            ("pyproject.toml", _get(pyproject, "tool", "pytest") is not None),
            ("setup.cfg", setup_cfg.has_section("tool:pytest")),
            ("tox.ini", "[pytest]" in (tree.read_text("tox.ini") or "")),
            ("conftest.py", bool(tree.glob_names("conftest.py"))),
            ("dependencies", "pytest" in deps),
        ]
        for source, present in pytest_sources:
            if present:
                test_framework = "pytest"
                evidence.append(Evidence(claim="pytest configured", source=source))
                break
        test_files = [
            f
            for f in tree.files
            if f.endswith(".py")
            and (f.rsplit("/", 1)[-1].startswith("test_") or f.endswith("_test.py"))
        ]
        if test_framework is None and test_files:
            imports_pytest = any("import pytest" in (tree.read_text(f) or "") for f in test_files)
            test_framework = "pytest" if imports_pytest else "unittest"
            evidence.append(
                Evidence(claim=f"test files found ({test_framework} style)", source=test_files[0])
            )
        if test_framework == "pytest" and "pytest" not in deps:
            # The sandbox install step must provide the runner even if the repo omits it.
            install.append(["python", "-m", "pip", "install", "pytest"])

        test_dirs = sorted({f.rsplit("/", 1)[0] for f in test_files if "/" in f})
        test_command: Command | None = None
        if test_framework == "pytest":
            test_command = ["python", "-m", "pytest"]
        elif test_framework == "unittest":
            test_command = ["python", "-m", "unittest", "discover"]

        # ---- lint / format / typecheck (only if the project configures them)
        lint: Command | None = None
        fmt: Command | None = None
        if (
            _get(pyproject, "tool", "ruff") is not None
            or tree.exists("ruff.toml")
            or tree.exists(".ruff.toml")
        ):
            lint = ["ruff", "check", "."]
            fmt = ["ruff", "format", "--check", "."]
            evidence.append(Evidence(claim="ruff configured", source="pyproject.toml"))
        elif tree.exists(".flake8") or setup_cfg.has_section("flake8"):
            lint = ["python", "-m", "flake8"]
            evidence.append(Evidence(claim="flake8 configured", source=".flake8/setup.cfg"))
        if fmt is None and _get(pyproject, "tool", "black") is not None:
            fmt = ["python", "-m", "black", "--check", "."]
            evidence.append(Evidence(claim="black configured", source="pyproject.toml"))

        typecheck: Command | None = None
        if (
            _get(pyproject, "tool", "mypy") is not None
            or tree.exists("mypy.ini")
            or tree.exists(".mypy.ini")
            or setup_cfg.has_section("mypy")
        ):
            typecheck = ["python", "-m", "mypy", "."]
            evidence.append(Evidence(claim="mypy configured", source="pyproject.toml/mypy.ini"))

        # ---- framework, entry points, layout
        framework = next((f for f in _FRAMEWORKS if f in deps), None)
        if framework:
            evidence.append(Evidence(claim=f"{framework} dependency", source="dependencies"))
        entry_points: list[str] = []
        scripts = _get(pyproject, "project", "scripts")
        if isinstance(scripts, dict):
            entry_points += [f"{k} = {v}" for k, v in scripts.items()]
        entry_points += [f for f in tree.files if f.endswith("__main__.py") or f == "manage.py"]

        package_dirs = sorted(
            {f.rsplit("/", 1)[0] for f in tree.files if f.endswith("/__init__.py")}
        )
        top_packages = sorted({d for d in package_dirs if d.count("/") <= 1})
        key_dirs = sorted(set(top_packages) | set(test_dirs[:5]))

        confidence: Literal["high", "medium", "low"] = (
            "high" if test_framework == "pytest" and dependency_files else "medium"
        )
        if test_framework is None:
            confidence = "low"
        supported = test_framework == "pytest"
        return RepoProfile(
            languages=dict(tree.language_counts),
            primary_language="python",
            adapter=self.name,
            supported=supported,
            unsupported_reason=None
            if supported
            else "v1 supports pytest-based Python projects only"
            if test_framework
            else "no tests or test framework detected",
            framework=framework,
            package_manager=package_manager,
            dependency_files=sorted(set(dependency_files)),
            test_framework=test_framework,
            install_commands=install,
            test_command=test_command,
            lint_command=lint,
            format_check_command=fmt,
            typecheck_command=typecheck,
            entry_points=entry_points,
            key_directories=key_dirs,
            test_directories=test_dirs,
            evidence=evidence,
            confidence=confidence,
        )

    @staticmethod
    def _setup_cfg(tree: RepoTree) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        text = tree.read_text("setup.cfg")
        if text:
            try:
                parser.read_string(text)
            except configparser.Error:
                return configparser.ConfigParser(interpolation=None)
        return parser

    @staticmethod
    def _dependencies(  # noqa: PLR0912 - one branch per dependency declaration format
        tree: RepoTree,
        pyproject: dict[str, Any],
        setup_cfg: configparser.ConfigParser,
        dependency_files: list[str],
    ) -> set[str]:
        lines: list[str] = []
        for key in ("dependencies",):
            value = _get(pyproject, "project", key)
            if isinstance(value, list):
                lines += [str(v) for v in value]
        for group in (_get(pyproject, "project", "optional-dependencies") or {}).values():
            if isinstance(group, list):
                lines += [str(v) for v in group]
        for group in (_get(pyproject, "dependency-groups") or {}).values():
            if isinstance(group, list):
                lines += [str(v) for v in group if isinstance(v, str)]
        poetry = _get(pyproject, "tool", "poetry") or {}
        for key in ("dependencies", "dev-dependencies"):
            if isinstance(poetry.get(key), dict):
                lines += list(poetry[key])
        for group in (poetry.get("group") or {}).values():
            if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                lines += list(group["dependencies"])
        for f in dependency_files:
            if f.endswith((".txt", ".in")):
                lines += (tree.read_text(f) or "").splitlines()
        if setup_cfg.has_option("options", "install_requires"):
            lines += setup_cfg.get("options", "install_requires").splitlines()
        if setup_cfg.has_section("options.extras_require"):
            for _key, value in setup_cfg.items("options.extras_require"):
                lines += value.splitlines()
        return _requirement_names(lines)

    @staticmethod
    def _test_extras(pyproject: dict[str, Any], setup_cfg: configparser.ConfigParser) -> list[str]:
        names: set[str] = set()
        optional = _get(pyproject, "project", "optional-dependencies")
        if isinstance(optional, dict):
            names |= set(optional)
        if setup_cfg.has_section("options.extras_require"):
            names |= {k for k, _ in setup_cfg.items("options.extras_require")}
        return sorted(n for n in names if n in {"test", "tests", "testing", "dev"})


DEFAULT_ADAPTERS: tuple[LanguageAdapter, ...] = (PythonAdapter(),)
