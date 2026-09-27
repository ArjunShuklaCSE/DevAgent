"""Tool failures. The ``code`` is stable and shown to the agent as a structured error."""

from core.tools import CallStatus

# Codes that mean "not allowed" rather than "went wrong": recorded as ``denied``.
DENIED_CODES = frozenset(
    {
        "path_outside_workspace",
        "symlink_escape",
        "symlink_write_denied",
        "protected_path",
        "read_only_path",
        "blocked_path",
        "executable_not_allowed",
        "module_not_allowed",
        "argument_not_allowed",
        "path_not_allowed",
        "invalid_argument",
        "too_many_args",
        "argument_too_long",
        "empty_command",
    }
)


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> CallStatus:
        return CallStatus.DENIED if self.code in DENIED_CODES else CallStatus.ERROR
