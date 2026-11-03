"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { CommsEntry } from "@/lib/types";

interface Suggestion {
  word: string;
  display: string;
  count: number;
  heard_as: string | null;
}

/** Reason chips: what came of an utterance, as a stamped tag. */
const CHIP: Record<string, { label: string; cls: string; title: string }> = {
  dispatched: { label: "dispatched", cls: "border-ok/60 text-ok", title: "Acted on" },
  "rejected-low-conf": { label: "rejected · low conf", cls: "border-bad/70 text-bad", title: "Under MIN_DISPATCH_CONF: never dispatched" },
  "rejected-unintelligible": { label: "rejected · garbled", cls: "border-bad/70 text-bad", title: "The classifier said it was unintelligible" },
  "rejected-unknown": { label: "rejected · unknown", cls: "border-bad/50 text-bad", title: "Not a known command" },
  rejected: { label: "rejected", cls: "border-bad/50 text-bad", title: "Not dispatched" },
  "ignored-speaking": { label: "ignored · speaking", cls: "border-line-strong text-mute", title: "Said while Athena was talking" },
  "ignored-muted": { label: "ignored · muted", cls: "border-line-strong text-mute", title: "Said while muted" },
  "ignored-busy": { label: "ignored · busy", cls: "border-line-strong text-mute", title: "Said while the briefing wasn't listening" },
  ignored: { label: "ignored", cls: "border-line-strong text-mute", title: "Not acted on" },
  held: { label: "held", cls: "border-accent/60 text-accent", title: "On the confirm card" },
  received: { label: "received", cls: "border-line-strong text-dim", title: "Being worked out" },
  cancelled: { label: "cancelled", cls: "border-line-strong text-mute line-through", title: "Cancelled before it was sent" },
  failed: { label: "failed", cls: "border-bad/70 text-bad", title: "Dispatch raised an error" },
};

/** Shown by default: what was acted on or refused (and anything in flight). Ignored and cancelled hide. */
const ACTIONABLE = new Set(["dispatched", "rejected", "failed", "held", "received"]);

function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Words corrected 3+ times: offer them for VOCAB in voice.conf (whisper's prompt). */
function Suggestions({ version }: { version: number }) {
  const [list, setList] = useState<Suggestion[]>([]);
  const load = useCallback(() => {
    api.get<{ suggestions: Suggestion[] }>("/api/vocab").then((r) => setList(r.suggestions), () => {});
  }, []);
  useEffect(() => {
    load();
  }, [load, version]);
  if (!list.length) return null;
  return (
    <div className="border-b border-line bg-accent/[0.05] px-5 py-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-accent">Often misheard: add to VOCAB?</div>
      {list.map((s) => (
        <div key={s.word} className="mt-2 flex items-baseline gap-3 text-[13px]">
          <span className="font-medium text-fg">{s.display}</span>
          <span className="min-w-0 flex-1 truncate text-xs text-mute">
            corrected {s.count}×{s.heard_as ? `, heard as “${s.heard_as}”` : ""}
          </span>
          <button
            className="text-xs text-fg underline decoration-line-strong underline-offset-4 hover:decoration-fg"
            onClick={() => api.post("/api/vocab", { word: s.display }).then(load)}
          >
            Add
          </button>
          <button className="text-xs text-mute hover:text-fg" onClick={() => api.post("/api/vocab/dismiss", { word: s.word }).then(load)}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}

function Row({ e, open, onOpen }: { e: CommsEntry; open: boolean; onOpen: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rerun = async (text?: string) => {
    setBusy(true);
    try {
      await api.post(`/api/comms/${e.id}/rerun`, text ? { text } : {});
      setEdit(null);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className={`relative border-b border-dashed border-line-strong ${open ? "bg-raised" : "hover:bg-raised/50"}`}>
      {e.flagged && <span className="absolute inset-y-0 left-0 w-[3px] bg-bad/80" title="low confidence" />}
      <button className="block w-full px-5 py-3 text-left" onClick={onOpen}>
        <div className="flex items-center gap-2.5 font-mono text-[10px] uppercase tracking-wider">
          <span className="tabular-nums normal-case text-work">{clock(e.ts)}</span>
          <span className="text-mute">{e.source}</span>
          {e.confidence !== null && (
            <span className={e.flagged ? "text-bad" : "text-faint"} title="whisper confidence">
              {Math.round(e.confidence * 100)}%{e.flagged ? " low" : ""}
            </span>
          )}
          {(() => {
            const c = CHIP[e.reason] ?? CHIP[e.status] ?? CHIP.received;
            return (
              <span title={c.title} className={`stamp ml-auto border px-1.5 py-px text-[9.5px] ${c.cls}`}>
                {c.label}
              </span>
            );
          })()}
        </div>
        {e.heard && (
          <div className="mt-1.5 text-[13px] text-mute">
            heard <span className="line-through decoration-bad/60">{e.heard}</span>
          </div>
        )}
        <div className={`mt-1 text-[14px] leading-[1.45] ${e.corrected_by ? "text-dim" : "text-fg"}`}>{e.text}</div>
        <div className="mt-1.5 truncate text-xs text-mute">
          {e.action ? (
            <>
              <span className="text-dim">{e.action}</span>
              {e.via ? ` / ${e.via}` : ""}
              {e.target ? ` → ${e.target}` : ""}
              {e.outcome ? <span className="text-faint"> · {e.outcome}</span> : null}
            </>
          ) : (
            e.outcome ?? "…"
          )}
        </div>
      </button>
      {open && (
        <div className="px-5 pb-3">
          {edit === null ? (
            <div className="flex gap-3 text-xs">
              <button disabled={busy} className="text-fg hover:underline disabled:opacity-40" onClick={() => rerun()}>
                Re-send
              </button>
              <button className="text-fg hover:underline" onClick={() => setEdit(e.text)}>
                Edit and re-run
              </button>
              {e.corrected_by ? <span className="ml-auto text-faint">corrected in #{e.corrected_by}</span> : null}
            </div>
          ) : (
            <form
              className="flex gap-2"
              onSubmit={(ev) => {
                ev.preventDefault();
                if (edit.trim()) rerun(edit.trim());
              }}
            >
              <input
                autoFocus
                value={edit}
                onChange={(ev) => setEdit(ev.target.value)}
                onKeyDown={(ev) => ev.key === "Escape" && (ev.stopPropagation(), setEdit(null))}
                className="h-8 min-w-0 flex-1 rounded-[6px] border border-line-strong bg-sunk px-2 text-[13px] outline-none focus:border-dim"
              />
              <button
                type="submit"
                disabled={busy || !edit.trim()}
                className="h-8 rounded-[6px] bg-fg px-3 text-[13px] font-medium text-ground disabled:opacity-40"
              >
                Run
              </button>
            </form>
          )}
        </div>
      )}
    </li>
  );
}

/** Every utterance: what was heard, how sure whisper was, what it was taken to mean, where it went. */
export function Comms({ entries }: { entries: Record<number, CommsEntry> }) {
  const [open, setOpen] = useState<number | null>(null);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const list = useMemo(() => Object.values(entries).sort((a, b) => b.id - a.id), [entries]);
  const flagged = list.filter((e) => e.flagged).length;
  const hidden = list.filter((e) => !ACTIONABLE.has(e.status)).length;
  const shown = list.filter((e) => (!flaggedOnly || e.flagged) && (showIgnored || ACTIONABLE.has(e.status)));
  const corrections = list.filter((e) => e.heard).length;
  return (
    <>
      <div className="flex items-center gap-4 border-b border-line px-5 py-2 text-xs">
        <span className="font-mono text-[11px] text-mute">{list.length} utterances</span>
        <button
          onClick={() => setFlaggedOnly(!flaggedOnly)}
          className={`font-mono text-[11px] ${flaggedOnly ? "text-bad" : flagged ? "text-dim hover:text-fg" : "text-faint"}`}
          title="Confidence under STT_FLAG_BELOW (voice.conf)"
        >
          {flagged} low confidence{flaggedOnly ? " (showing)" : ""}
        </button>
        <button
          onClick={() => setShowIgnored(!showIgnored)}
          aria-pressed={showIgnored}
          className={`ml-auto font-mono text-[11px] ${showIgnored ? "text-fg" : "text-mute hover:text-fg"}`}
          title="Ignored and cancelled utterances (ignored ones are purged after COMMS_RETENTION_DAYS)"
        >
          {showIgnored ? "hide" : "show"} ignored ({hidden})
        </button>
      </div>
      <Suggestions version={corrections} />
      <ol className="radio-log min-h-0 flex-1 overflow-y-auto">
        {shown.length === 0 && (
          <li className="px-5 py-10 text-center text-[13px] text-mute">
            {flaggedOnly
              ? "Nothing under the confidence threshold."
              : hidden && !showIgnored
                ? `Nothing acted on yet. ${hidden} ignored or cancelled hidden.`
                : "Nothing said yet. Hold Space and talk."}
          </li>
        )}
        {shown.map((e) => (
          <Row key={e.id} e={e} open={open === e.id} onOpen={() => setOpen(open === e.id ? null : e.id)} />
        ))}
      </ol>
    </>
  );
}
