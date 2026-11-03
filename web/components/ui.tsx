"use client";
import { useEffect, useSyncExternalStore } from "react";
import type { SessionState } from "@/lib/types";

// FLIGHT_SPEAK=on (vario.conf or env, read at build time by next.config.ts): aviation wording for the
// state labels on screen. Labels only; the data and anything spoken keep the plain words.
const FLIGHT = process.env.NEXT_PUBLIC_FLIGHT_SPEAK === "on";

export const STATE_STYLE: Record<SessionState, { label: string; dot: string; bar: string; text: string }> = {
  working: { label: FLIGHT ? "in flight" : "working", dot: "bg-work pulse", bar: "bg-work/70", text: "text-work" },
  waiting: { label: FLIGHT ? "holding" : "waiting on you", dot: "bg-accent blink", bar: "bg-accent", text: "text-accent" },
  done: { label: FLIGHT ? "landed" : "done", dot: "bg-ok", bar: "bg-transparent", text: "text-ok" },
  error: { label: FLIGHT ? "mayday" : "error", dot: "bg-bad", bar: "bg-bad", text: "text-bad" },
};

export function StateBadge({ state }: { state: SessionState }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE.done;
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${s.text}`}>
      <span className={`h-1.5 w-1.5 ${s.dot}`} />
      {s.label}
    </span>
  );
}

/** Per-session identity colour, stable for the session's life: pencil tones defined per theme in
    globals.css (--sess-0..7). Sun and rust stay reserved for "needs you" and "error". */
const SESSION_HUES = Array.from({ length: 8 }, (_, i) => `var(--sess-${i})`);

function hueIndex(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return Math.abs(h) % SESSION_HUES.length;
}

export function sessionColor(id: string) {
  return SESSION_HUES[hueIndex(id)];
}

/** Colours for the sessions on screen, all different while there are enough hues. Oldest first keep
    their hashed hue; a later session that collides takes the next free one, so colours don't shuffle. */
export function assignColors(sessions: { id: string; created_at: number }[]) {
  const out: Record<string, string> = {};
  const taken = new Set<number>();
  for (const s of [...sessions].sort((a, b) => a.created_at - b.created_at || a.id.localeCompare(b.id))) {
    let i = hueIndex(s.id);
    for (let k = 0; k < SESSION_HUES.length && taken.has(i); k++) i = (i + 1) % SESSION_HUES.length;
    taken.add(i);
    if (taken.size >= SESSION_HUES.length) taken.clear();
    out[s.id] = SESSION_HUES[i];
  }
  return out;
}

/** "claude-opus-5-5" -> "opus 5.5", "claude-sonnet-4-5-20250929" -> "sonnet 4.5" */
export function shortModel(m: string) {
  const r = m.replace(/^claude-/, "").replace(/-\d{8}$/, "").split("-");
  const name = r.find((p) => /[a-z]/.test(p)) ?? r[0];
  const ver = r.filter((p) => /^\d+$/.test(p)).join(".");
  return ver ? `${name} ${ver}` : name;
}

/** 83395 -> "83k", 1_200_000 -> "1.2M" */
export function fmtTokens(n: number) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

/** "/Users/me/code/x" -> "~/code/x" */
export function tildify(path: string) {
  return path.replace(/^\/Users\/[^/]+/, "~");
}

/** One-line preview text: drop markdown marks so "**Before** `dev`" reads "Before dev". */
export function plain(text: string) {
  return text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|`)/g, "")
    .replace(/^#+\s*/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Small-caps section label. */
export function Label({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <h3 className={`text-[11px] font-medium uppercase tracking-[0.14em] text-mute ${className}`}>{children}</h3>;
}

// Shared 1s clock as an external store: no Date.now() during render/prerender (Cache Components),
// and every component ticks together.
let clockNow = 0;
const clockSubs = new Set<() => void>();
let clockTimer: ReturnType<typeof setInterval> | null = null;

function subscribeClock(cb: () => void) {
  clockSubs.add(cb);
  if (!clockTimer) {
    const tick = () => {
      clockNow = Date.now() / 1000;
      clockSubs.forEach((f) => f());
    };
    tick();
    clockTimer = setInterval(tick, 1000);
  }
  return () => {
    clockSubs.delete(cb);
    if (clockSubs.size === 0 && clockTimer) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}

/** Current time in seconds, updated every second (0 during server render). */
export function useNow() {
  return useSyncExternalStore(subscribeClock, () => clockNow, () => 0);
}

export function fmtElapsed(secs: number) {
  if (!isFinite(secs) || secs < 0) return "";
  const s = Math.floor(secs);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

export function Button({
  children,
  variant = "default",
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "ghost" | "danger" }) {
  const v = {
    default: "border border-line-strong text-fg hover:border-dim hover:bg-hover",
    primary: "bg-fg text-ground hover:bg-fg/85",
    ghost: "text-dim hover:bg-hover hover:text-fg",
    danger: "border border-bad/60 text-bad hover:bg-bad/10",
  }[variant];
  return (
    <button
      className={`inline-flex items-center gap-1.5 h-8 rounded-[6px] px-3 text-[13px] font-medium transition-colors disabled:opacity-40 disabled:pointer-events-none ${v} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-[var(--scrim)] p-4 pt-[10vh]" onMouseDown={onClose}>
      <div
        className="w-full max-w-2xl rounded-[8px] border-[1.5px] border-line-strong bg-raised shadow-float"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="font-serif text-[18px] text-fg">{title}</h2>
          <button onClick={onClose} className="font-mono text-xs text-mute hover:text-fg" aria-label="Close">
            esc
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
