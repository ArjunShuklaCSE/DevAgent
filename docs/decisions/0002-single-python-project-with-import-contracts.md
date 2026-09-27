# 0002. One Python project; dependency rules enforced by import-linter

- Status: Accepted
- Date: 2026-09-27

## Context
The spec requires `tools/`, `sandbox/`, `llm/` never import `backend/` or `agent/`,
and `agent/` to depend on interfaces. Rules that live only in prose erode.

## Decision
- One `pyproject.toml` at the root, managed by **uv**, packaging all top-level Python
  packages (`backend`, `agent`, `tools`, `sandbox`, `llm`, `evaluation`, `database`,
  `mcp_server`) into one distribution.
- **import-linter** contracts in `pyproject.toml` enforce the dependency rule in
  pre-commit and CI:
  - `tools`, `sandbox`, `llm` ✗→ `backend`, `agent`
  - `agent` ✗→ `backend`
  - `database` ✗→ any application layer
- **mypy strict applies to every Python package and the tests**, not only the four the
  spec lists. The spec's narrower scope would let `database`, `evaluation` and
  `mcp_server` drift; strict everywhere costs little on a greenfield codebase.

## Alternatives
- uv workspace with one project per package: real package boundaries, but eight
  `pyproject.toml` files, per-package versions and slower iteration for a single-
  developer project. import-linter gives the boundary guarantee without that cost.
  Revisit if packages need independent release (e.g. publishing `mcp_server`).

## Consequences
- A forbidden import fails CI with a precise message.
- All services share one dependency set; the worker image carries API deps and vice versa.
