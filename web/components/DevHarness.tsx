"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { CommsEntry, ReplySent } from "@/lib/types";
import type { MicEvent } from "@/lib/usePushToTalk";

/**
 * Text stand-in for the microphone. Posts to /api/voice/utterance, the same endpoint the mic,
 * push-to-talk and briefing auto-listen use, so it exercises the identical classify/route pipeline.
 */
export function DevHarness({
  selectedSessionId,
  replies,
  micLog,
  micSupported,
  comms,
  onClose,
}: {
  selectedSessionId: string | null;
  replies: ReplySent[];
  micLog: MicEvent[];
  micSupported: boolean;
  /** The Comms log: its low-confidence spoken entries can be replayed with their original confidence. */
  comms: Record<number, CommsEntry>;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [conf, setConf] = useState("");
  const [log, setLog] = useState<{ text: string; res: unknown }[]>([]);
  // replayable: anything whisper heard under 40% (the default MIN_DISPATCH_CONF), oldest first
  const low = Object.values(comms)
    .filter((e) => e.confidence !== null && e.confidence < 0.4 && e.source !== "harness")
    .sort((a, b) => a.id - b.id)
    .slice(-6);

  /** confidence: as whisper reported it (0-1); null = typed, which the confidence gate doesn't apply to */
  const inject = async (t: string, confidence: number | null, label = t) => {
    setText("");
    try {
      const res = await api.post("/api/voice/utterance", { text: t, source: "harness", selected_session_id: selectedSessionId, confidence });
      setLog((l) => [{ text: label, res }, ...l].slice(0, 8));
    } catch (e) {
      setLog((l) => [{ text: label, res: String(e) }, ...l].slice(0, 8));
    }
  };
  const typedConf = conf.trim() === "" ? null : Math.max(0, Math.min(1, Number(conf.replace("%", "")) / (Number(conf.replace("%", "")) > 1 ? 100 : 1)));

  return (
    <div className="border-t border-line bg-sunk px-5 py-2.5 text-xs">
      <div className="flex items-center gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-mute">Harness</span>
        <form
          className="flex flex-1 gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) inject(text.trim(), typedConf !== null && isFinite(typedConf) ? typedConf : null);
          }}
        >
          <input
            data-testid="harness-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='same pipeline as the mic — e.g. "yes", "billing, use staging", "note that …", "later", "stop briefing"'
            className="min-w-0 flex-1 rounded-[6px] border border-line-strong bg-ground px-2 py-1 font-mono text-xs outline-none focus:border-dim"
          />
          <input
            value={conf}
            onChange={(e) => setConf(e.target.value)}
            aria-label="Simulated whisper confidence (blank = typed)"
            placeholder="conf %"
            className="w-16 rounded-[6px] border border-line-strong bg-ground px-2 py-1 font-mono text-xs outline-none focus:border-dim"
          />
          <button type="submit" className="rounded-[6px] border border-line-strong px-3 py-1 font-medium text-dim hover:text-fg">
            Inject
          </button>
        </form>
        <button onClick={onClose} className="font-mono text-[11px] text-mute hover:text-fg">
          close
        </button>
      </div>
      {low.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-2 font-mono text-[11px] text-mute">
          <span>replay low-confidence:</span>
          {low.map((e) => (
            <button
              key={e.id}
              onClick={() => inject(e.text, e.confidence, `replay #${e.id} @${Math.round((e.confidence ?? 0) * 100)}%: ${e.text}`)}
              className="rounded-[5px] border border-line-strong px-1.5 text-dim hover:border-dim hover:text-fg"
              title={`Re-post "${e.text}" with its original ${Math.round((e.confidence ?? 0) * 100)}% confidence`}
            >
              #{e.id} {Math.round((e.confidence ?? 0) * 100)}% &ldquo;{e.text.slice(0, 24)}&rdquo;
            </button>
          ))}
          {low.length > 1 && (
            <button
              onClick={async () => {
                for (const e of low) await inject(e.text, e.confidence, `replay #${e.id} @${Math.round((e.confidence ?? 0) * 100)}%: ${e.text}`);
              }}
              className="text-fg underline decoration-line-strong underline-offset-4"
            >
              replay all
            </button>
          )}
        </div>
      )}
      <div className="mt-1.5 truncate font-mono text-[11px] text-mute">
        mic: {micSupported ? "Web Speech available" : "Web Speech NOT available in this browser (use Chrome)"} ·{" "}
        {micLog.length === 0
          ? "no mic events yet — hold Space (with focus outside text boxes) and speak"
          : micLog
              .slice(0, 6)
              .map((m) => `${m.event}${m.detail ? `(${m.detail})` : ""}`)
              .join(" ← ")}
      </div>
      {(log.length > 0 || replies.length > 0) && (
        <div className="mt-2 grid gap-1 font-mono text-[11px] text-dim lg:grid-cols-2">
          <div>
            {log.map((l, i) => (
              <div key={i} className="truncate">
                <span className="text-fg">› {l.text}</span> → {JSON.stringify(l.res)}
              </div>
            ))}
          </div>
          <div>
            {replies.slice(0, 8).map((r, i) => (
              <div key={i} className="truncate">
                <span className="text-ok">reply_sent</span> [{r.source}] “{r.utterance}” → {r.action} → {r.routed_to ?? "—"} · “{r.say}”
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
