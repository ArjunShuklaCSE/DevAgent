import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SystemStatus } from "./system-status";

describe("SystemStatus", () => {
  it("shows each dependency with latency when healthy", () => {
    render(
      <SystemStatus
        health={{
          kind: "reported",
          httpStatus: 200,
          report: {
            status: "ok",
            version: "0.1.0",
            checks: {
              database: { status: "ok", latency_ms: 1.23, error: null },
              redis: { status: "ok", latency_ms: 0.4, error: null },
            },
          },
        }}
      />,
    );
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("1.2 ms")).toBeInTheDocument();
  });

  it("shows the failing dependency's error when degraded", () => {
    render(
      <SystemStatus
        health={{
          kind: "reported",
          httpStatus: 503,
          report: {
            status: "degraded",
            version: "0.1.0",
            checks: { redis: { status: "error", latency_ms: 2000, error: "timed out after 2s" } },
          },
        }}
      />,
    );
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("timed out after 2s")).toBeInTheDocument();
  });

  it("shows an alert when the API is unreachable", () => {
    render(<SystemStatus health={{ kind: "unreachable", message: "API returned HTTP 502" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("API returned HTTP 502");
  });

  it("shows the empty state when there are no checks", () => {
    render(
      <SystemStatus
        health={{
          kind: "reported",
          httpStatus: 200,
          report: { status: "ok", version: "0.1.0", checks: {} },
        }}
      />,
    );
    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
