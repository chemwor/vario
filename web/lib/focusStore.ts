"use client";
import { useCallback, useSyncExternalStore } from "react";

/** What fills the right-hand slot: a session's drawer or a report's evidence panel, never both.
    Lives in sessionStorage, so a websocket reconnect or a reload brings back the same view. */
export interface Focus {
  session: string | null;
  report: number | null;
}

const KEY = "vario-focus";
const NONE: Focus = { session: null, report: null };
const subs = new Set<() => void>();
let cache: { raw: string | null; value: Focus } = { raw: null, value: NONE };

function read(): Focus {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(KEY);
  } catch {}
  if (raw !== cache.raw) {
    let value = NONE;
    try {
      const f = JSON.parse(raw ?? "null");
      if (f) value = { session: f.session ?? null, report: f.report ?? null };
    } catch {}
    cache = { raw, value };
  }
  return cache.value;
}

function write(f: Focus) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(f));
  } catch {
    cache = { raw: JSON.stringify(f), value: f };   // storage blocked: keep it in memory
  }
  subs.forEach((cb) => cb());
}

function subscribe(cb: () => void) {
  subs.add(cb);
  return () => subs.delete(cb);
}

export function useFocus() {
  const focus = useSyncExternalStore(subscribe, read, () => NONE);
  const setSession = useCallback((id: string | null) => write({ session: id, report: null }), []);
  const setReport = useCallback((id: number | null) => write({ session: null, report: id }), []);
  const clear = useCallback(() => write(NONE), []);
  return { focus, setSession, setReport, clear };
}
