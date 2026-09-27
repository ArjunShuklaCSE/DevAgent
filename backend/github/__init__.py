"""GitHub access for the backend only.

Nothing under ``agent/``, ``tools/``, ``sandbox/`` or ``llm/`` can import this package
(import-linter contracts), so the model has no path to a GitHub write API (spec 6.7).
"""
