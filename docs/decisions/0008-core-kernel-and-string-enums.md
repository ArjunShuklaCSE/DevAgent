# 0008. Shared `core` kernel; enums stored as strings with CHECK constraints

- Status: Accepted
- Date: 2026-09-27

## Context
Run statuses, the transition table and event payload types are needed by `database`
(column types), `agent` (the executor walks the state machine), and `backend` (API and
SSE). The spec's layout has no neutral home for them, and `database` must not import
`agent`, nor `agent` import `backend`.

## Decision
- Add a top-level **`core/`** package holding `RunStatus`, `allowed_transitions`,
  `ensure_transition` and the typed event payloads. An import-linter contract forbids
  `core` from importing any other DevAgent package.
- Store enums as `VARCHAR(32)` with a named `CHECK` constraint (`native_enum=False`)
  instead of Postgres `ENUM` types.

## Alternatives
- Put the state machine in `agent/` as the spec suggests: `database` could then not use
  `RunStatus` as a column type without breaking the dependency rule.
- Native Postgres enums: adding a value needs `ALTER TYPE … ADD VALUE`, which cannot run
  inside a transaction on older servers and cannot be removed on downgrade.

## Consequences
- One source of truth for statuses; the DB CHECK constraint rejects anything else.
- Adding an enum value is a normal migration that drops and recreates the constraint.
- Alembic's autogenerate does not compare CHECK constraints, so enum changes must be
  written into migrations by hand (the migration test catches missing tables/columns only).
