"""Host-side workspace preparation: safe cloning and size/file-count limits.

Runs in the worker; never executes repository code. Must not import ``backend`` or
``agent`` (enforced by import-linter).
"""
