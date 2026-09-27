import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { EvaluationView } from "./evaluation-view";

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EvaluationView />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("EvaluationView", () => {
  it("shows the empty state when no evaluation has run", async () => {
    vi.spyOn(api, "evalRuns").mockResolvedValue([]);
    renderView();
    expect(await screen.findByText("No data yet")).toBeInTheDocument();
  });

  it("shows an error when the API is unreachable", async () => {
    vi.spyOn(api, "evalRuns").mockRejectedValue(new Error("API unreachable"));
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("API unreachable");
  });
});
