"use client";

import { useSyncExternalStore } from "react";

const KEY = "devagent-theme";

/** Runs before paint (inlined in <head>) so a saved light theme never flashes dark. */
export const THEME_SCRIPT = `try{if(localStorage.getItem("${KEY}")==="light")document.documentElement.classList.remove("dark")}catch(e){}`;

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const isDark = () => document.documentElement.classList.contains("dark");

export function ThemeToggle() {
  const dark = useSyncExternalStore(subscribe, isDark, () => true);
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
  };
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
    >
      {dark ? (
        <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden>
          <path d="M10 2a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1Zm4 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm-.46 4.95.7.71a1 1 0 0 0 1.42-1.41l-.71-.71a1 1 0 0 0-1.41 1.41ZM16 9a1 1 0 1 1 0 2h-1a1 1 0 1 1 0-2h1Zm-1.05-4.54a1 1 0 0 0-1.41 1.41l.7.71a1 1 0 0 0 1.42-1.42l-.71-.7ZM10 15a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1Zm-4.95-.46.71-.7a1 1 0 1 1 1.41 1.41l-.7.71a1 1 0 1 1-1.42-1.42ZM5 10a1 1 0 0 1-1 1H3a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1Zm.05-5.54a1 1 0 0 0-1.41 1.42l.7.7A1 1 0 0 0 5.76 5.17l-.7-.71Z" />
        </svg>
      ) : (
        <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden>
          <path d="M17.29 13.29A8 8 0 0 1 6.71 2.71a8 8 0 1 0 10.58 10.58Z" />
        </svg>
      )}
    </button>
  );
}
