"use client";
import { useSyncExternalStore } from "react";

/** What the mic is doing right now, for the live caption bar. Updated ~12x a second while recording,
 *  so it lives outside React state: only the components that read it re-render. */
export type MicPhase = "idle" | "listening" | "processing";

export interface LiveCaption {
  phase: MicPhase;
  level: number | null; // 0..1 input level; null when the engine can't report one (Web Speech)
  voiced: boolean; // the level detector thinks you're talking
  text: string; // partial while listening, final once processed
  final: boolean;
  confidence: number | null;
  at: number; // ms timestamp of the last change of phase or text
}

let state: LiveCaption = { phase: "idle", level: null, voiced: false, text: "", final: false, confidence: null, at: 0 };
const subs = new Set<() => void>();

export function setLive(patch: Partial<LiveCaption>) {
  const touched = patch.phase !== undefined || patch.text !== undefined;
  state = { ...state, ...patch, at: touched ? Date.now() : state.at };
  subs.forEach((f) => f());
}

export function getLive() {
  return state;
}

export function useLive(): LiveCaption {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
    () => state,
    () => state,
  );
}
