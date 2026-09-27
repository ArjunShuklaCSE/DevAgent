# 0019. MCP server: read-only code tools and run control, no approval

- Status: Accepted (Phase 10)
- Date: 2026-09-27

## Context
The spec asks for DevAgent's tool registry to be exposed as an MCP server: read-only
tools plus run control. The usual MCP client is another LLM agent (an IDE assistant,
say), so whatever the server exposes, a prompt-injected model may call.

## Decision
- **Low-level MCP `Server`** from the official Python SDK (`mcp` 2.x), served over
  stdio (`python -m mcp_server`, or the `devagent-mcp` script). The low-level API lets
  us publish the registry's own JSON schemas instead of re-declaring every tool.
- **Code tools are the registry's `read` tools**: `list_tree`, `search_files`,
  `search_text`, `find_symbol`, `find_references` and `read_file`. They run
  in-process with the same path safety, limits and truncation as in an agent run,
  against a git checkout given with `--root`. Every call is logged.
  - Tools that write files or execute code (`edit_file`, `create_file`,
    `run_command`, `run_tests`) are not exposed: executing code outside the sandbox is
    exactly what DevAgent exists to avoid.
  - `git_diff` is not exposed either: it stages the working tree into the index,
    which is fine in a throwaway run workspace but not in a developer's checkout.
  - File listing no longer writes DevAgent's excludes to `.git/info/exclude`; it
    passes them to `git ls-files` instead, so reading a checkout changes nothing in
    it (tested).
- **Run control calls the REST API**: `list_repositories`, `add_repository`,
  `list_runs`, `get_run`, `start_run`, `cancel_run`, `get_run_diff`,
  `export_run_trace`. The server needs no database or Docker access, and the API stays
  the single place where rules are enforced. With sign-in enabled, the session cookie
  is passed through `DEVAGENT_MCP_SESSION`.
- **No approve, reject or publish tool.** Approval binds a person to a diff hash. An
  MCP client could otherwise start a run and approve its own output, turning an
  injected instruction into a pull request. These actions stay in the dashboard.
- **Tool annotations** mark read tools `readOnlyHint`, and `cancel_run`
  `destructiveHint`. The server's instructions tell clients that results are untrusted
  repository data.
- **Errors are tool results, not crashes.** API errors return the API's error envelope
  with `isError`. Invalid arguments are rejected before any request is made.

## Alternatives
- **FastMCP decorators.** Simpler for hand-written tools, but they derive schemas from
  Python signatures, which would duplicate the registry's Pydantic models.
- **Streamable HTTP transport.** Useful for remote clients, but it needs its own
  authentication. stdio keeps the server local to the developer's machine. Adding HTTP
  later is a transport change.
- **Exposing sandboxed `run_tests`.** It would need the worker's Docker access and a
  per-session workspace lifecycle. Starting a run already gives a client sandboxed
  execution with full recording.

## Consequences
- Code tools need a git checkout: search lists files through `git ls-files`, as in a
  run workspace.
- Clients can start runs that cost money. The run budget applies to them as to any
  run, and `start_run` accepts `max_cost_usd`.
