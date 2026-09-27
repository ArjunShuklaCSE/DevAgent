/** Live run events over Server-Sent Events, with the connection state the UI shows. */
"use client";

import { useEffect, useRef, useState } from "react";

export interface RunEvent {
  seq: number;
  run_id: string;
  step_id: string | null;
  type: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

export const EVENT_TYPES = [
  "status_changed",
  "step_started",
  "step_completed",
  "tool_call",
  "command_output",
  "test_result",
  "llm_usage",
  "error",
] as const;

export interface RunEventsOptions {
  /** Stop listening (and don't reconnect) once this returns true, e.g. on a terminal status. */
  isFinal?: (event: RunEvent) => boolean;
  onEvent?: (event: RunEvent) => void;
  enabled?: boolean;
}

/**
 * Subscribe to `/api/v1/runs/{id}/events`. EventSource reconnects on its own and sends
 * Last-Event-ID, and the server replays from there, so events are neither lost nor
 * duplicated; `seq` is still checked in case a replay overlaps.
 */
export function useRunEvents(runId: string, options: RunEventsOptions = {}) {
  const { enabled = true } = options;
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<ConnectionState>("connecting");
  const callbacks = useRef(options);
  useEffect(() => {
    callbacks.current = options;
  });

  useEffect(() => {
    if (!enabled) return;
    let lastSeq = 0;
    let finished = false;
    const source = new EventSource(`/api/v1/runs/${runId}/events`);
    const handle = (message: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(message.data) as RunEvent;
      } catch {
        return;
      }
      if (event.seq <= lastSeq) return;
      lastSeq = event.seq;
      setEvents((previous) => [...previous, event]);
      callbacks.current.onEvent?.(event);
      if (callbacks.current.isFinal?.(event)) {
        finished = true;
        source.close();
        setState("closed");
      }
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, handle);
    source.onopen = () => setState("live");
    source.onerror = () => {
      if (finished) return;
      // CLOSED means the browser gave up (e.g. a 404); CONNECTING means it is retrying.
      setState(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    };
    return () => {
      finished = true;
      source.close();
    };
  }, [runId, enabled]);

  return { events, state };
}
