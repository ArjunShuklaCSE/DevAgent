"""Shared domain kernel: run statuses, the state machine's transition table, event types.

Imports nothing from the rest of DevAgent, so every layer (agent, backend, database,
evaluation) can depend on it without violating the dependency rules.
"""
