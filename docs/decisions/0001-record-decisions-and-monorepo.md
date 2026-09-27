# 0001. Record decisions as ADRs; one monorepo

- Status: Accepted
- Date: 2026-09-27

## Context
DevAgent spans a Python backend/agent, a Next.js frontend, Docker images and an
evaluation harness. Work happens in phases across sessions, so decisions must be
recoverable without chat history.

## Decision
- Keep everything in one repository with the layout from the spec (Section 4.2).
- Record significant choices as short ADRs in `docs/decisions/NNNN-title.md`
  (context, decision, alternatives, consequences). `PROGRESS.md` tracks state.

## Alternatives
- Separate repos for frontend/backend: more release overhead, API changes need
  coordinated PRs. Rejected at this scale.

## Consequences
- One CI pipeline, atomic cross-cutting changes.
- The frontend has its own `package.json`/lockfile under `frontend/`; it is not a pnpm
  workspace root because there is only one JS package (revisit if a shared TS package appears).
