"use client";
import { useLive } from "@/lib/captionStore";
import type { BriefingState } from "@/lib/types";

const PHASE = {
  off: { label: "mic off", cls: "text-mute" },
  idle: { label: "waiting for reports", cls: "text-dim" },
  speaking: { label: "speaking, mic closed", cls: "text-work" },
  listening: { label: "listening", cls: "text-bad" },
  routing: { label: "routing", cls: "text-accent" },
  processing: { label: "transcribing", cls: "text-accent" },
} as const;

/** Status-bar text for the mic: what briefing is doing, or push-to-talk. */
export function MicPill({ b, listening, now }: { b: BriefingState | null; listening: boolean; now: number }) {
  const live = useLive();
  const phase = b?.armed ? b.phase : "off";
  const ptt = listening && phase === "off";
  const transcribing = live.phase === "processing";
  const p = transcribing ? PHASE.processing : ptt ? PHASE.listening : PHASE[phase] ?? PHASE.off;
  const left = phase === "listening" && b?.listen_until ? Math.max(0, Math.ceil(b.listen_until - now)) : null;
  return (
    <span className={`flex items-center gap-1.5 tabular-nums ${p.cls}`}>
      {(ptt || phase === "listening") && <span className="blink h-1.5 w-1.5 rounded-full bg-bad" />}
      mic: {ptt && !transcribing ? "push-to-talk" : p.label}
      {left !== null && ` ${left}s`}
    </span>
  );
}

export function BriefingBanner({ b, now, interim }: { b: BriefingState; now: number; interim: string }) {
  const left = b.phase === "listening" && b.listen_until ? Math.max(0, b.listen_until - now) : 0;
  const pct = b.phase === "listening" ? Math.min(100, (left / b.listen_s) * 100) : 0;
  return (
    <div className="relative border-b border-line bg-raised">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 py-2.5 pl-5 pr-5">
        <span className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-accent">
          <span className="blink h-1.5 w-1.5 rounded-full bg-sun" /> Briefing
        </span>
        <span className="font-mono text-[11px] text-mute">{b.remaining} left</span>
        {b.current ? (
          <span className="min-w-0 flex-1 truncate text-[14px] text-fg">
            <span className="font-medium">{b.current.project}</span>
            <span className="text-mute"> / {b.current.type.replace("_", " ")} / </span>
            {b.current.summary}
          </span>
        ) : (
          <span className="flex-1 text-[13px] text-mute">{b.phase === "idle" ? "Queue empty. New reports are read as they arrive." : ""}</span>
        )}
        {b.phase === "listening" && (
          <span className="max-w-[22rem] truncate font-mono text-xs text-dim">{interim || "yes / no / skip / later / or reply"}</span>
        )}
      </div>
      <span className="absolute inset-y-0 left-0 w-[3px] bg-sun" />
      {b.phase === "listening" && (
        <div className="absolute inset-x-0 bottom-0 h-[2px] bg-line">
          <div className="h-full bg-sun transition-[width] duration-1000 ease-linear" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
