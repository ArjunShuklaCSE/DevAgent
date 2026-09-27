import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "./api";

describe("toApiError", () => {
  it("reads the API error envelope", async () => {
    const response = Response.json(
      {
        error: {
          code: "stale_diff",
          message: "The diff changed since it was reviewed",
          details: { a: 1 },
        },
      },
      { status: 409 },
    );
    const error = await toApiError(response);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "stale_diff", details: { a: 1 } });
    expect(error.message).toBe("The diff changed since it was reviewed");
  });

  it("falls back to the HTTP status for non-JSON bodies", async () => {
    const error = await toApiError(new Response("<html>bad gateway</html>", { status: 502 }));
    expect(error).toMatchObject({ status: 502, code: "http_error" });
  });
});
