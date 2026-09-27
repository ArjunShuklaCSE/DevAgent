import { describe, expect, it } from "vitest";

import { parseAnsi } from "./ansi";

describe("parseAnsi", () => {
  it("returns plain text unchanged", () => {
    expect(parseAnsi("collected 5 items\n")).toEqual([
      { text: "collected 5 items\n", color: null, bold: false, dim: false },
    ]);
  });

  it("maps SGR colors and bold, and resets", () => {
    const segments = parseAnsi("\u001b[32m\u001b[1m5 passed\u001b[0m in 0.02s");
    expect(segments).toEqual([
      { text: "5 passed", color: "green", bold: true, dim: false },
      { text: " in 0.02s", color: null, bold: false, dim: false },
    ]);
  });

  it("drops non-SGR escapes such as cursor movement and OSC titles", () => {
    const text = parseAnsi("a\u001b[2Kb\u001b]0;title\u0007c")
      .map((s) => s.text)
      .join("");
    expect(text).toBe("abc");
  });

  it("never produces markup: HTML in output stays text", () => {
    const [segment] = parseAnsi("\u001b[31m<img src=x onerror=alert(1)>\u001b[0m");
    expect(segment).toEqual({
      text: "<img src=x onerror=alert(1)>",
      color: "red",
      bold: false,
      dim: false,
    });
  });
});
