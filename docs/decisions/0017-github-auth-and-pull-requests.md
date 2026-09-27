# 0017. GitHub: OAuth sign-in, user token or fine-grained PAT for writes, Git Data API for PRs

- Status: Accepted (Phase 8)
- Date: 2026-09-27

## Context
Spec sections 2, 6.5, 6.7 and 8 set the requirements:
- Users sign in with GitHub.
- DevAgent imports issues.
- After a human approves the exact diff, it opens a **draft** PR from
  `devagent/issue-<n>-<slug>`.
- It falls back to a patch file when it cannot write.
- Tokens never reach the workspace, the sandbox, prompts or logs.

For repository writes, the spec asks for a fine-grained PAT or a GitHub App installation
token: document both and implement one.

## Decision
- **Sign-in is a GitHub OAuth app (web flow).**
  - The `state` value is bound to a Fernet-sealed, 10-minute cookie scoped to
    `/api/v1/auth`.
  - The session is a sealed, HttpOnly, SameSite=Lax cookie holding only the user id.
    It is Secure when the public URL is https.
  - The `next` redirect only accepts same-site relative paths.
  - When the OAuth app is configured, every run and repository endpoint requires a
    session. Without it (the local, single-user default), the API stays open and is
    bound to localhost by Compose.
- **Which token writes.** The approving user's OAuth token (scope `repo`) is used. It
  is stored Fernet-encrypted in `github_credentials` and deleted on sign-out.
  - If that token is missing, the server's **fine-grained PAT**
    (`DEVAGENT_GITHUB_TOKEN`) is used. This covers setups without an OAuth app.
  - With neither, the run is delivered as a patch.
  - A GitHub App installation token is documented as the alternative: it gives
    finer permissions and a bot author, but needs an app, a private key and an
    installation per repository. The credential model already has
    `kind=app_installation`, so adding it means a new token source, not a new flow.
- **The PR is built with the Git Data API; nothing is cloned with the token.**
  - The publisher checks the diff's SHA-256 against the approval.
  - It fetches the touched files at the run's base commit through the contents API
    and runs `git apply --check` and then `git apply` in a private temporary directory.
  - From the result it creates blobs, then a tree on the base tree (deletions,
    renames and existing file modes are kept), then a commit whose parent is the base
    commit, authored by the configured bot identity.
  - Then it creates the branch and opens a draft PR.
  - The token only ever appears in the `Authorization` header of API requests. Tests
    check the requests, the logs and the agent workspace.
- **Base branch moved.** The branch is always based on the commit the fix was
  validated on.
  - If the default branch has since changed a file the diff touches, publishing
    stops with `base_moved` and asks for a new run.
  - Otherwise the PR opens and its body says the branch is behind.
- **Failures keep the approval.** A new transition, `creating_pr → approved`, records a
  failed attempt: missing permission, rate limit (with its reset time), base moved, or
  a patch that does not apply.
  - The failure is stored in `result.delivery`, and the attempt shows as a failed
    `creating_pr` step.
  - `GET /runs/{id}/patch` always serves a `git am` patch, and
    `POST /runs/{id}/publish` retries.
  - Branch-name collisions get `-2`, `-3` and so on.
- **The model cannot reach any of this.** `backend.github` sits in the web layer, which
  `agent`, `tools`, `sandbox` and `llm` cannot import (import-linter). Publishing runs in
  the worker's `publish_run` job, which approval queues.

## Consequences
- Cloning still uses anonymous HTTPS, so only public repositories can be *solved*. A
  private repository would need the token inside the clone, and keeping it out of
  `.git/config` in the workspace is future work. PRs and issue import work for any
  repository the token can reach.
- The Git Data API costs one request per touched file plus a few per PR. That is fine
  for issue-sized diffs; binary changes are refused and offered as a patch.
- The acceptance item "PR opened on a test repo you control" needs a real token and
  repository from the owner. The code path is exercised end to end against an
  in-memory GitHub (`tests/github_fake.py`) with real `git apply`.
