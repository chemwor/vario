"use client";
import { useEffect, useState } from "react";
import { useLive } from "@/lib/captionStore";
import type { PendingState } from "@/lib/types";

const BARS = 14;

/** Input level as a row of bars; lit bars turn bright while the detector thinks you're talking. */
function Meter({ level, voiced }: { level: number | null; voiced: boolean }) {
  if (level === null) return <span className="font-mono text-[10px] uppercase tracking-wider text-faint">no level</span>;
  const lit = Math.round(level * BARS);
  return (
    <span className="flex h-4 items-end gap-[2px]" aria-label={`input level ${Math.round(level * 100)}%`}>
      {Array.from({ length: BARS }, (_, i) => (
        <span
          key={i}
          className={`w-[3px] transition-[height] duration-75 ${i < lit ? (voiced ? "bg-fg" : "bg-dim") : "bg-line-strong"}`}
          style={{ height: `${30 + (i / BARS) * 70}%` }}
        />
      ))}
    </span>
  );
}

const PHASE = {
  idle: { label: "Idle", cls: "text-mute", dot: "bg-faint" },
  listening: { label: "Listening", cls: "text-bad", dot: "bg-bad blink" },
  processing: { label: "Processing", cls: "text-accent", dot: "bg-accent blink" },
} as const;

/**
 * Bottom overlay: what the mic hears as you speak, whether it thinks you've stopped, and the confirm card
 * that holds an utterance before it's dispatched ("no", "cancel" or Esc cancels; silence sends it, except an
 * explicit hold, which only a click or a spoken "confirm" sends).
 */
export function CaptionBar({
  pending,
  lastDone,
  engine,
  onCancel,
  onSendNow,
}: {
  pending: PendingState | null;
  lastDone: { id: number; outcome: "dispatch" | "cancel"; why: string; at: number } | null;
  engine: string;
  onCancel: (id: number) => void;
  onSendNow: (id: number) => void;
}) {
  const live = useLive();
  // own 10 Hz clock (not Date.now() in render): drives the countdown and the fade-out
  const [now, setNow] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(t);
  }, []);

  const recent = now - live.at < 4000;
  const doneRecent = lastDone && now - lastDone.at < 2500 ? lastDone : null;
  const show = now > 0 && (pending || live.phase !== "idle" || (recent && live.text) || doneRecent);
  if (!show) return null;
  const ph = PHASE[live.phase];
  const left = pending ? Math.max(0, pending.deadline * 1000 - now) : 0;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-9 z-40 flex justify-center px-4">
      <div className="pointer-events-auto w-full max-w-[56rem] rounded-[8px] border-[1.5px] border-line-strong bg-raised/95 shadow-float backdrop-blur">
        <div className="flex items-center gap-4 border-b border-line px-5 py-2 font-mono text-[11px] uppercase tracking-wider">
          <span className={`flex items-center gap-2 ${ph.cls}`}>
            <span className={`h-2 w-2 rounded-full ${ph.dot}`} />
            {ph.label}
          </span>
          <Meter level={live.phase === "listening" ? live.level : null} voiced={live.voiced} />
          {live.phase === "listening" && live.voiced && <span className="normal-case tracking-normal text-dim">hearing you</span>}
          {live.phase === "listening" && !live.voiced && live.level !== null && (
            <span className="normal-case tracking-normal text-faint">quiet</span>
          )}
          {live.final && live.confidence !== null && live.phase === "idle" && (
            <span className="normal-case tracking-normal text-faint">confidence {Math.round(live.confidence * 100)}%</span>
          )}
          <span className="ml-auto normal-case tracking-normal text-faint">{engine}</span>
        </div>

        {pending ? (
          <div className="relative px-5 pb-4 pt-3">
            <div className="flex items-baseline justify-between gap-4">
              <span className={`font-mono text-[11px] uppercase tracking-wider ${pending.explicit ? "text-bad" : "text-accent"}`}>
                {pending.reading
                  ? "Reading back"
                  : pending.explicit
                    ? `Needs confirmation: heard at ${pending.confidence === null ? "unknown" : `${Math.round(pending.confidence * 100)}%`} confidence`
                    : `Sending in ${(left / 1000).toFixed(1)}s`}
              </span>
              <span className="font-mono text-[11px] text-faint">
                {pending.explicit ? <>won&rsquo;t send on silence · say &ldquo;confirm&rdquo; or click Confirm · esc cancels</> : <>say &ldquo;no&rdquo; or &ldquo;cancel&rdquo;, or press esc</>}
              </span>
            </div>
            <p className="mt-2 text-[24px] leading-snug text-fg">&ldquo;{pending.text}&rdquo;</p>
            {pending.spoken && (
              <p className="mt-1 text-[15px] italic text-dim">
                <span className="mr-2 font-mono text-[10px] not-italic uppercase tracking-wider text-faint">understood</span>
                {pending.spoken}
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="border border-accent/50 px-1.5 py-px font-mono text-[11px] uppercase tracking-wider text-accent">
                {pending.action}
              </span>
              <span className="text-[15px] text-dim">
                {pending.target ? (
                  <>
                    to <span className="font-medium text-fg">{pending.target}</span>
                  </>
                ) : (
                  pending.desc
                )}
              </span>
              <span className="ml-auto flex gap-2">
                <button
                  onClick={() => onCancel(pending.id)}
                  className="h-8 rounded-[6px] border border-line-strong px-3 text-[13px] text-fg hover:border-dim hover:bg-hover"
                >
                  Cancel <kbd className="ml-1 font-mono text-[10px] text-mute">esc</kbd>
                </button>
                <button
                  onClick={() => onSendNow(pending.id)}
                  className="h-8 rounded-[6px] bg-fg px-3 text-[13px] font-medium text-ground hover:bg-fg/85"
                >
                  {pending.explicit ? "Confirm" : "Send now"}
                </button>
              </span>
            </div>
            <div className="absolute inset-x-0 bottom-0 h-[3px] bg-line">
              <div
                className={`h-full transition-[width] duration-100 ease-linear ${pending.explicit ? "bg-bad/60" : "bg-accent"}`}
                style={{ width: pending.reading ? "100%" : `${Math.min(100, (left / Math.max(1, pending.hold_ms)) * 100)}%` }}
              />
            </div>
          </div>
        ) : doneRecent && !(live.phase !== "idle") ? (
          <p className={`px-5 py-3 text-[20px] ${doneRecent.outcome === "cancel" ? "text-mute line-through" : "text-fg"}`}>
            {doneRecent.outcome === "cancel" ? `Cancelled${doneRecent.why ? `: ${doneRecent.why}` : ""}` : "Sent."}
          </p>
        ) : (
          <p
            className={`min-h-[4.25rem] px-5 py-3 text-[26px] leading-snug ${
              live.phase === "listening" ? "text-fg/80" : live.phase === "processing" ? "text-dim" : "text-fg"
            }`}
          >
            {live.text || (live.phase === "listening" ? <span className="text-faint">Go ahead…</span> : null)}
            {live.phase === "listening" && live.text && <span className="blink ml-1 inline-block h-6 w-[3px] bg-dim align-middle" />}
          </p>
        )}
      </div>
    </div>
  );
}
