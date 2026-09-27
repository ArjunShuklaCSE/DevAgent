import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useRunEvents, type RunEvent } from "./events";

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((m: MessageEvent<string>) => void)[]>();
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (m: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
  emit(event: Partial<RunEvent> & { seq: number; type: string }) {
    const data = JSON.stringify({
      run_id: "r",
      step_id: null,
      created_at: "",
      payload: {},
      ...event,
    });
    for (const fn of this.listeners.get(event.type) ?? [])
      fn(new MessageEvent(event.type, { data }));
  }
}

const original = globalThis.EventSource;
beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
});
afterEach(() => {
  globalThis.EventSource = original;
});

describe("useRunEvents", () => {
  it("collects events in order, ignores replayed duplicates, and closes when final", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", {
        isFinal: (e) => e.type === "status_changed" && e.payload.to_status === "failed",
      }),
    );
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toBe("/api/v1/runs/run-1/events");
    expect(result.current.state).toBe("connecting");

    act(() => {
      source.readyState = FakeEventSource.OPEN;
      source.onopen?.();
      source.emit({ seq: 1, type: "step_started" });
      source.emit({ seq: 2, type: "command_output" });
    });
    expect(result.current.state).toBe("live");

    act(() => {
      source.readyState = FakeEventSource.CONNECTING;
      source.onerror?.();
    });
    expect(result.current.state).toBe("reconnecting");

    act(() => {
      source.onopen?.();
      source.emit({ seq: 2, type: "command_output" }); // overlap after reconnect
      source.emit({ seq: 3, type: "status_changed", payload: { to_status: "failed" } });
    });
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(result.current.state).toBe("closed");
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("does not connect until enabled", () => {
    renderHook(() => useRunEvents("run-1", { enabled: false }));
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
