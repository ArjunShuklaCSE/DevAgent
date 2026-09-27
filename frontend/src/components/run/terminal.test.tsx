import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/lib/events";

import { Terminal } from "./terminal";

const output = (seq: number, text: string, stream = "stdout"): RunEvent => ({
  seq,
  run_id: "r",
  step_id: null,
  type: "command_output",
  created_at: "2026-09-27T10:00:00Z",
  payload: { stream, text },
});

describe("Terminal", () => {
  it("has an empty state", () => {
    render(<Terminal events={[]} />);
    expect(screen.getByText("No command output yet.")).toBeInTheDocument();
  });

  it("renders sandbox output as text, with ANSI colors as classes", () => {
    const { container } = render(
      <Terminal
        events={[
          output(1, "$ python -m pytest\n"),
          { ...output(2, "ignored"), type: "tool_call" },
          output(3, "\u001b[32m5 passed\u001b[0m <script>alert(1)</script>\n"),
        ]}
      />,
    );
    const terminal = screen.getByLabelText("Terminal output");
    expect(terminal).toHaveTextContent("$ python -m pytest 5 passed <script>alert(1)</script>");
    expect(terminal).not.toHaveTextContent("ignored");
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("5 passed")).toHaveClass("text-emerald-400");
  });
});
