# Changelog

## 0.1.0

First release of DevAgent: give it a Python repository and a GitHub issue, and it reproduces the bug with a failing test, fixes it in a no-network Docker sandbox, and opens a draft pull request only after a person approves the exact diff.

- Explicit state machine from cloning to pull request, with a bounded debug and retry loop.
- Reproduction first: the fix must turn a test that failed on the original code into a passing one, and the editor can't modify that test.
- One sandbox container per command: non-root, read-only root filesystem, resource limits, no network; Docker reached only through a filtering proxy.
- Typed tools with path safety and an allowlisted command policy.
- Budgets for steps, fix attempts, tokens, cost and wall-clock time, enforced before each action.
- Approval bound to the diff's SHA-256; draft PRs built through the GitHub Git Data API, with a `git am` patch as fallback.
- Next.js dashboard with live timeline and terminal over SSE, diff review, model-call details and run history.
- Evaluation harness with hidden tests, a failure taxonomy, Wilson intervals and a debug-loop ablation.
- MCP server with read-only code tools and run control.
- Anthropic and OpenAI adapters, plus a deterministic scripted model so the test suite runs without API keys.
