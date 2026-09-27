"use client";

import { useEffect, useMemo, useRef } from "react";

import { parseAnsi } from "@/lib/ansi";
import type { RunEvent } from "@/lib/events";

const COLOR_CLASS: Record<string, string> = {
  black: "text-zinc-500",
  red: "text-red-400",
  green: "text-emerald-400",
  yellow: "text-amber-300",
  blue: "text-sky-400",
  magenta: "text-fuchsia-400",
  cyan: "text-cyan-300",
  white: "text-zinc-100",
  gray: "text-zinc-500",
};

/** Sandbox output as text nodes only; ANSI colors become classes, never markup. */
export function Terminal({ events }: { events: RunEvent[] }) {
  const output = useMemo(
    () =>
      events
        .filter((e) => e.type === "command_output")
        .map((e) => ({
          seq: e.seq,
          stderr: e.payload.stream === "stderr",
          text: String(e.payload.text ?? ""),
        })),
    [events],
  );
  const box = useRef<HTMLPreElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    // Follow new output inside the box only; never scroll the page.
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [output.length]);

  if (output.length === 0) {
    return <p className="p-4 text-sm text-zinc-500">No command output yet.</p>;
  }
  return (
    <pre
      ref={box}
      aria-label="Terminal output"
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      className="max-h-[44rem] overflow-auto rounded-b-lg bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-zinc-300"
    >
      {output.map((chunk) => (
        <span key={chunk.seq} className={chunk.stderr ? "text-amber-200/90" : undefined}>
          {parseAnsi(chunk.text).map((segment, i) => (
            <span
              key={i}
              className={[
                segment.color ? COLOR_CLASS[segment.color] : "",
                segment.bold ? "font-bold" : "",
                segment.dim ? "opacity-60" : "",
              ].join(" ")}
            >
              {segment.text}
            </span>
          ))}
        </span>
      ))}
    </pre>
  );
}
