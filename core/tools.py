"""Tool vocabulary shared by the tools package, the database and the API."""

from enum import StrEnum


class ToolCapability(StrEnum):
    READ = "read"
    WRITE_WORKSPACE = "write_workspace"
    EXECUTE_SANDBOX = "execute_sandbox"


class CallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"


class ChangeType(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
