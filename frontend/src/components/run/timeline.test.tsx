import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Step } from "@/lib/api";

import { Timeline } from "./timeline";

const base: Omit<Step, "id" | "sequence" | "state" | "status"> = {
  synthetic: false,
  summary: null,
  rationale: null,
  output: {},
  error: null,
  started_at: "2026-09-27T10:00:00Z",
  finished_at: null,
  duration_ms: null,
};

describe("Timeline", () => {
  it("shows a waiting message before the first step", () => {
    render(<Timeline steps={[]} />);
    expect(screen.getByText(/Waiting for the worker/)).toBeInTheDocument();
  });

  it("lists steps with outcome, duration, summary, error and rationale", () => {
    render(
      <Timeline
        steps={[
          {
            ...base,
            id: "1",
            sequence: 1,
            state: "reproducing",
            status: "completed",
            duration_ms: 1_800,
            summary: "test fails",
            rationale: "because",
          },
          {
            ...base,
            id: "2",
            sequence: 2,
            state: "testing",
            status: "failed",
            duration_ms: 1_700,
            error: { code: "tests_failed", message: "still red" },
          },
          { ...base, id: "3", sequence: 3, state: "debugging", status: "running" },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Reproducing");
    expect(items[0]).toHaveTextContent("1.8 s");
    expect(items[0]).toHaveTextContent("Rationale");
    expect(items[1]).toHaveTextContent("failed");
    expect(items[1]).toHaveTextContent("tests_failed: still red");
    expect(items[2]).toHaveTextContent("running");
  });
});
