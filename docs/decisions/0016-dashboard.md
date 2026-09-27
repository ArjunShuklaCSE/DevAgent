# 0016. Dashboard: same-origin API proxy, SSE in the browser, diff-bound approval

- Status: Accepted (Phase 7)
- Date: 2026-09-27

## Context
Spec section 11 asks for five pages (home, runs, run detail, diff and approval,
evaluation) with live updates, a dark default with a light toggle, and loading, empty,
error and reconnecting states. The API already streams run events over SSE with
`Last-Event-ID` resume (ADR 0005). The browser needs to reach it, and approving a diff
must not approve a different one.

## Decision
- **The browser only talks to the web server.** A catch-all route handler
  (`app/api/v1/[...path]/route.ts`) forwards requests to `DEVAGENT_API_INTERNAL_URL`
  and streams the response body back unchanged, so SSE stays live. It forwards only
  `accept`, `content-type` and `last-event-id`. The API needs no CORS and can stay on the
  internal Compose network. When the API is down, the proxy answers with the API's
  own error envelope (`api_unreachable`, HTTP 502).
- **Live state comes from events, details from queries.** `useRunEvents` wraps
  `EventSource`. The browser reconnects and resends `Last-Event-ID`; the server replays
  from there. The hook also drops any event whose `seq` it has already seen. Each event
  type invalidates the matching TanStack Query cache (the run, steps, tool calls, LLM
  calls or test runs), so tables refetch from the API rather than being rebuilt from
  event payloads. The terminal is built from `command_output` events, which the server
  replays from the start of the run.
- **Connection state is visible.** The run header shows Connecting, Live,
  Reconnecting (with a banner) or Finished. The hook closes the stream on a terminal
  status so the browser does not reconnect forever.
- **Sandbox output is text, never markup.** A small SGR parser (`lib/ansi.ts`) turns
  ANSI colors into CSS classes on text nodes and drops all other escape sequences.
  Nothing from the sandbox or the model is rendered with `dangerouslySetInnerHTML`.
- **Approval is bound to the reviewed diff.** The review page sends the SHA-256 of
  the diff it displayed. `POST /runs/{id}/approve` returns 409 `stale_diff` if the stored
  diff has changed. Approvals and rejections are rows in `approvals`, with a
  `status_changed` event.
- **Theme.** Tailwind's `dark` variant is keyed on the `.dark` class, which is on
  `<html>` by default. A small inline script in `<head>` removes it before first paint
  when `localStorage` says "light", so there is no flash.
- **Smoke test against the real stack.** Playwright (`frontend/e2e/smoke.spec.ts`)
  seeds a run through the API with the `scripted` model, follows it live to
  `awaiting_approval`, opens every tab, reviews and approves the diff, checks the theme
  toggle, and checks the error state for an unknown run. With `DEVAGENT_SCREENSHOTS=1` it
  writes the README screenshots to `docs/images/`. CI runs it after the integration tests.

## Consequences
- Every browser request makes one extra hop through the Next.js server. For a
  single-user tool on one host this cost does not matter, and it removes CORS and
  exposed-port configuration.
- Tables poll every 3 seconds while a run is active, as well as refetching on events.
  This covers events that don't map one-to-one to rows.
- The evaluation page is an empty state until Phase 9 produces reports.
