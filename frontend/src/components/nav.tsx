"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "./theme-toggle";
import { cx } from "./ui";

const LINKS = [
  { href: "/", label: "New run" },
  { href: "/runs", label: "Runs" },
  { href: "/evaluation", label: "Evaluation" },
];

export function Nav() {
  const pathname = usePathname();
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <div className="mx-auto flex h-12 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-6 place-items-center rounded bg-indigo-600 font-mono text-xs text-white">
            D
          </span>
          DevAgent
        </Link>
        <nav className="flex gap-1 text-sm">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={cx(
                "rounded-md px-2.5 py-1",
                active(link.href)
                  ? "bg-zinc-100 font-medium dark:bg-zinc-800"
                  : "text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100",
              )}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
