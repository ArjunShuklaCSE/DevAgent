/**
 * Minimal ANSI SGR parser for terminal output (pytest, pip). Supports reset, bold,
 * dim and the 16 standard foreground colors; other escape sequences are dropped.
 * Output is plain text segments, so nothing from the sandbox is ever rendered as HTML.
 */

export interface AnsiSegment {
  text: string;
  color: string | null;
  bold: boolean;
  dim: boolean;
}

const COLORS: Record<number, string> = {
  30: "black",
  31: "red",
  32: "green",
  33: "yellow",
  34: "blue",
  35: "magenta",
  36: "cyan",
  37: "white",
  90: "gray",
  91: "red",
  92: "green",
  93: "yellow",
  94: "blue",
  95: "magenta",
  96: "cyan",
  97: "white",
};

// CSI sequences (ESC [ ... final byte) and OSC sequences (ESC ] ... BEL/ST).
const ESCAPE = /\u001b\[([0-9;]*)([A-Za-z])|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g;

export function parseAnsi(input: string): AnsiSegment[] {
  const segments: AnsiSegment[] = [];
  let state = { color: null as string | null, bold: false, dim: false };
  let last = 0;
  const push = (text: string) => {
    if (!text) return;
    const previous = segments.at(-1);
    if (
      previous &&
      previous.color === state.color &&
      previous.bold === state.bold &&
      previous.dim === state.dim
    ) {
      previous.text += text;
    } else {
      segments.push({ text, ...state });
    }
  };
  for (const match of input.matchAll(ESCAPE)) {
    push(input.slice(last, match.index));
    last = match.index + match[0].length;
    if (match[2] !== "m") continue; // not SGR: drop
    const codes = (match[1] || "0").split(";").map((c) => Number.parseInt(c || "0", 10));
    for (const code of codes) {
      if (code === 0) state = { color: null, bold: false, dim: false };
      else if (code === 1) state = { ...state, bold: true };
      else if (code === 2) state = { ...state, dim: true };
      else if (code === 22) state = { ...state, bold: false, dim: false };
      else if (code === 39) state = { ...state, color: null };
      else if (COLORS[code]) state = { ...state, color: COLORS[code] };
    }
  }
  push(input.slice(last));
  return segments;
}
