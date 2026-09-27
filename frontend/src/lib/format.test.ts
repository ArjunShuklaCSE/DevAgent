import { describe, expect, it } from "vitest";

import { elapsedMs, formatCost, formatDuration, formatTokens, humanize, shortSha } from "./format";

describe("format", () => {
  it("formats durations across units", () => {
    expect(formatDuration(null)).toBe("–");
    expect(formatDuration(34)).toBe("34 ms");
    expect(formatDuration(1_700)).toBe("1.7 s");
    expect(formatDuration(15_200)).toBe("15 s");
    expect(formatDuration(600_000)).toBe("10m 00s");
    expect(formatDuration(3_720_000)).toBe("1h 02m");
  });

  it("measures elapsed time, open-ended while running", () => {
    expect(elapsedMs(null, null)).toBeNull();
    expect(elapsedMs("2026-09-27T10:00:00Z", "2026-09-27T10:00:05Z")).toBe(5_000);
    expect(elapsedMs("2026-09-27T10:00:00Z", null, Date.parse("2026-09-27T10:01:00Z"))).toBe(
      60_000,
    );
  });

  it("formats costs and tokens", () => {
    expect(formatCost("0")).toBe("$0.00");
    expect(formatCost("0.0042")).toBe("$0.0042");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatTokens(700)).toBe("700");
    expect(formatTokens(2_100)).toBe("2.1k");
    expect(formatTokens(45_000)).toBe("45k");
    expect(formatTokens(1_250_000)).toBe("1.25M");
  });

  it("humanizes enum values and shortens hashes", () => {
    expect(humanize("awaiting_approval")).toBe("Awaiting approval");
    expect(shortSha("8c40c5823a35deadbeef")).toBe("8c40c5823a35");
    expect(shortSha(null)).toBe("–");
  });
});
