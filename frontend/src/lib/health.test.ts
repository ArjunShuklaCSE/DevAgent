import { describe, expect, it, vi } from "vitest";

import { fetchSystemHealth, type HealthReport } from "./health";

const report: HealthReport = {
  status: "ok",
  version: "0.1.0",
  checks: {
    database: { status: "ok", latency_ms: 1.2, error: null },
    redis: { status: "ok", latency_ms: 0.4, error: null },
  },
};

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

describe("fetchSystemHealth", () => {
  it("returns the report on 200", async () => {
    const result = await fetchSystemHealth("http://api:8000", respond(200, report));
    expect(result).toEqual({ kind: "reported", httpStatus: 200, report });
  });

  it("treats 503 with a report as a degraded report, not an outage", async () => {
    const degraded = { ...report, status: "degraded" };
    const result = await fetchSystemHealth("http://api:8000", respond(503, degraded));
    expect(result.kind).toBe("reported");
  });

  it("calls the /health endpoint without caching", async () => {
    const fetchImpl = respond(200, report);
    await fetchSystemHealth("http://api:8000/", fetchImpl);
    const [url, init] = vi.mocked(fetchImpl).mock.calls[0]!;
    expect(String(url)).toBe("http://api:8000/health");
    expect(init?.cache).toBe("no-store");
  });

  it("reports network failures as unreachable", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const result = await fetchSystemHealth("http://api:8000", fetchImpl);
    expect(result).toEqual({ kind: "unreachable", message: "API request failed: fetch failed" });
  });

  it("rejects unexpected status codes and payloads", async () => {
    expect((await fetchSystemHealth("http://a", respond(500, report))).kind).toBe("unreachable");
    expect((await fetchSystemHealth("http://a", respond(200, { hi: 1 }))).kind).toBe("unreachable");
  });
});
