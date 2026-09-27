import { describe, expect, it } from "vitest";

import { percent, rateLabel, runName } from "./format";

describe("evaluation formatting", () => {
  it("shows a rate with its count and Wilson interval", () => {
    expect(rateLabel({ k: 1, n: 2, value: 0.5, ci_low: 0.0945, ci_high: 0.9055 })).toEqual({
      value: "50% (1/2)",
      hint: "95% CI 9% to 91%",
    });
  });

  it("never invents a number when nothing was scored", () => {
    expect(rateLabel({ k: 0, n: 0, value: null, ci_low: 0, ci_high: 1 })).toEqual({
      value: "n/a",
      hint: "no scored cases",
    });
    expect(percent(null)).toBe("n/a");
  });

  it("names runs by label, else by short id", () => {
    expect(runName({ label: "no-debug-loop", id: "abc" })).toBe("no-debug-loop");
    expect(runName({ label: null, id: "0123456789" })).toBe("01234567");
  });
});
