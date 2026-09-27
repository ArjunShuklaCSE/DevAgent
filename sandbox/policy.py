"""Command policy: which argv lists may run in the sandbox, and with what limits.

The policy file (``config/command_policy.yaml``) is parsed into frozen models once;
``CommandPolicy.check`` either returns the resolved limits for a command or raises
``PolicyViolationError`` with a stable code. See the YAML file for the rationale.
"""

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Profile = Literal["run", "install"]


class PolicyViolationError(Exception):
    """A command was rejected by the policy. ``code`` is stable and shown to the agent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Defaults(_Frozen):
    timeout_seconds: float = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_args: int = Field(gt=0)
    max_arg_length: int = Field(gt=0)


class ExecutableRule(_Frozen):
    aliases: tuple[str, ...] = ()
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_output_bytes: int | None = Field(default=None, gt=0)
    modules: tuple[str, ...] | None = None  # python only: allowed `-m` modules
    module_subcommands: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    deny_args: tuple[str, ...] = ()


class ProfileRules(_Frozen):
    network: bool
    executables: dict[str, ExecutableRule]


class PolicyDocument(_Frozen):
    version: Literal[1]
    defaults: Defaults
    allowed_path_roots: tuple[str, ...]
    profiles: dict[Profile, ProfileRules]


@dataclass(frozen=True)
class CommandLimits:
    """What the sandbox needs to run an accepted command."""

    executable: str
    timeout_seconds: float
    max_output_bytes: int
    network: bool


_PYTHON_NAMES = frozenset({"python", "python3"})


class CommandPolicy:
    def __init__(self, document: PolicyDocument) -> None:
        self._doc = document
        self._by_profile: dict[Profile, dict[str, tuple[str, ExecutableRule]]] = {}
        for profile, rules in document.profiles.items():
            names: dict[str, tuple[str, ExecutableRule]] = {}
            for name, rule in rules.executables.items():
                for alias in (name, *rule.aliases):
                    names[alias] = (name, rule)
            self._by_profile[profile] = names

    @classmethod
    def load(cls, path: Path) -> "CommandPolicy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(PolicyDocument.model_validate(raw))

    @property
    def document(self) -> PolicyDocument:
        return self._doc

    def check(
        self, argv: Sequence[str], profile: Profile, timeout_seconds: float | None = None
    ) -> CommandLimits:
        """Validate ``argv`` for ``profile``; ``timeout_seconds`` can only lower the limit."""
        defaults = self._doc.defaults
        if not argv:
            raise PolicyViolationError("empty_command", "command is empty")
        if len(argv) > defaults.max_args:
            raise PolicyViolationError("too_many_args", f"at most {defaults.max_args} arguments")
        for arg in argv:
            if "\x00" in arg:
                raise PolicyViolationError("invalid_argument", "arguments may not contain NUL")
            if len(arg) > defaults.max_arg_length:
                raise PolicyViolationError(
                    "argument_too_long", f"arguments are limited to {defaults.max_arg_length} chars"
                )

        command = argv[0]
        if "/" in command:
            raise PolicyViolationError(
                "executable_not_allowed", "run executables by name, not by path"
            )
        entry = self._by_profile.get(profile, {}).get(command)
        if entry is None:
            allowed = ", ".join(sorted(self._by_profile.get(profile, {})))
            raise PolicyViolationError(
                "executable_not_allowed", f"{command!r} is not allowed here (allowed: {allowed})"
            )
        name, rule = entry

        args = list(argv[1:])
        if name in _PYTHON_NAMES:
            self._check_python(args, rule)
        for arg in args:
            if _denied(arg, rule.deny_args):
                raise PolicyViolationError(
                    "argument_not_allowed", f"{arg!r} is not allowed for {name}"
                )
            self._check_path(arg)

        limit = rule.timeout_seconds or defaults.timeout_seconds
        if timeout_seconds is not None:
            limit = min(limit, timeout_seconds)
        return CommandLimits(
            executable=name,
            timeout_seconds=limit,
            max_output_bytes=rule.max_output_bytes or defaults.max_output_bytes,
            network=self._doc.profiles[profile].network,
        )

    def _check_python(self, args: list[str], rule: ExecutableRule) -> None:
        if not args:
            raise PolicyViolationError("argument_not_allowed", "interactive python is not allowed")
        first = args[0]
        if first == "-m":
            if len(args) < 2:  # noqa: PLR2004 - "-m" and the module name
                raise PolicyViolationError("argument_not_allowed", "python -m needs a module")
            module = args[1]
            if rule.modules is None or module not in rule.modules:
                raise PolicyViolationError(
                    "module_not_allowed", f"python -m {module} is not allowed here"
                )
            subcommands = rule.module_subcommands.get(module)
            if subcommands is not None and (len(args) < 3 or args[2] not in subcommands):  # noqa: PLR2004
                raise PolicyViolationError(
                    "argument_not_allowed",
                    f"python -m {module} only allows: {', '.join(subcommands)}",
                )
        elif first.startswith("-"):
            raise PolicyViolationError(
                "argument_not_allowed",
                f"python {first} is not allowed; write code to a file and run the file",
            )
        elif rule.modules is not None and not first.endswith(".py"):
            raise PolicyViolationError("argument_not_allowed", "python may only run .py files")

    def _check_path(self, arg: str) -> None:
        value = arg.split("=", 1)[1] if arg.startswith("--") and "=" in arg else arg
        if not (value.startswith(("/", "./", "../")) or "/.." in value or value == ".."):
            return
        resolved = posixpath.normpath(posixpath.join("/workspace", value))
        roots = self._doc.allowed_path_roots
        if not any(resolved == root or resolved.startswith(root + "/") for root in roots):
            raise PolicyViolationError(
                "path_not_allowed", f"{value!r} is outside the allowed paths ({', '.join(roots)})"
            )


def _denied(arg: str, deny: tuple[str, ...]) -> bool:
    for flag in deny:
        if arg == flag:
            return True
        if flag.startswith("--") and arg.startswith(flag + "="):
            return True
        is_short = len(flag) == 2 and flag[0] == "-" and flag[1] != "-"  # noqa: PLR2004
        if is_short and arg.startswith(flag) and not arg.startswith("--"):
            return True
    return False
