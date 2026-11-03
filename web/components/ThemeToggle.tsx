"use client";
import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";
const KEY = "vario-theme"; // pallas-theme: the pre-rename key, still read by layout.tsx
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function saved(): Theme | null {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

function apply(t: Theme) {
  document.documentElement.dataset.theme = t;
}

/** Pick and save a theme (the setup wizard uses this; the toggle below does the same). */
export function setTheme(t: Theme) {
  apply(t);
  try {
    localStorage.setItem(KEY, t);
  } catch {}
}

// The theme lives on <html data-theme> (set before paint in layout.tsx). Until the user picks one it
// follows the system setting live.
function subscribe(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const m = media();
  const onSystem = () => saved() || apply(m.matches ? "dark" : "light");
  m.addEventListener("change", onSystem);
  return () => {
    obs.disconnect();
    m.removeEventListener("change", onSystem);
  };
}

const current = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/** Daylight hangar / night flight. Saved to localStorage once chosen. */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, current, () => "light" as Theme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      onClick={() => {
        apply(next);
        try {
          localStorage.setItem(KEY, next);
        } catch {}
      }}
      title={theme === "dark" ? "Night flight. Switch to daylight" : "Daylight. Switch to night flight"}
      aria-label={`Switch to ${next} theme`}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] text-dim transition-colors hover:bg-hover hover:text-fg"
    >
      <svg viewBox="0 0 20 20" className="h-[17px] w-[17px]" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" aria-hidden="true">
        {theme === "dark" ? (
          <path d="M15.5 12.4 A6.2 6.2 0 1 1 7.6 4.5 A5 5 0 0 0 15.5 12.4 Z" />
        ) : (
          <>
            <circle cx="10" cy="10" r="3.4" />
            <path d="M10 2.2v2 M10 15.8v2 M2.2 10h2 M15.8 10h2 M4.5 4.5l1.4 1.4 M14.1 14.1l1.4 1.4 M4.5 15.5l1.4-1.4 M14.1 5.9l1.4-1.4" />
          </>
        )}
      </svg>
    </button>
  );
}
