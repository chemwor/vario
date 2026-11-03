"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { EvidenceItem, Report, Session, TEvent } from "@/lib/types";
import { DiffView, EvidenceList, ReplyBar, useEvidence } from "./Evidence";
import { TestChip, TestsPanel } from "./Tests";
import { Button, plain, StateBadge, tildify } from "./ui";

type FileDiff = Extract<EvidenceItem, { kind: "diff" }>;

/** Drawer UI state per session, kept in sessionStorage so a reconnect (or reload) doesn't reset it. */
function useDrawerState<T>(sid: string, key: string, initial: T): [T, (v: T) => void] {
  const k = `vario-drawer:${sid}:${key}`;
  const [v, setV] = useState<T>(() => {
    try {
      const raw = sessionStorage.getItem(k);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: T) => {
      setV(nv);
      try {
        sessionStorage.setItem(k, JSON.stringify(nv));
      } catch {}
    },
    [k],
  );
  return [v, set];
}

/** The open question a blocked session is waiting on: what it's about, and the answer controls. */
function PendingQuestion({ report }: { report: Report }) {
  const { ev, loading } = useEvidence(report.id);
  return (
    <div className="relative mx-5 mt-3 overflow-hidden rounded-[7px] border-[1.5px] border-sun/70 bg-raised shadow-card">
      <span className="absolute inset-y-0 left-0 w-[3px] bg-sun" />
      <div className="py-3 pl-4 pr-3">
        <div className="font-mono text-[10px] uppercase tracking-wider text-accent">Waiting on you</div>
        <p className="mt-1 text-[14px] leading-snug text-fg">{plain(report.summary)}</p>
        <details className="mt-2" open>
          <summary className="cursor-pointer font-mono text-[11px] text-mute hover:text-fg">evidence</summary>
          <div className="mt-2 max-h-[38vh] overflow-y-auto pr-1">
            <EvidenceList ev={ev} loading={loading} />
          </div>
        </details>
        <div className="mt-3">
          <ReplyBar report={report} />
        </div>
      </div>
    </div>
  );
}

/** Inline markdown only: `code` and **bold**. Everything stays a React text node. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2 ? (
          <code key={i} className="bg-hover px-1 py-px font-mono text-[12.5px] text-fg">{p.slice(1, -1)}</code>
        ) : p.startsWith("**") && p.endsWith("**") && p.length > 4 ? (
          <strong key={i} className="font-semibold text-fg">{p.slice(2, -2)}</strong>
        ) : (
          p
        ),
      )}
    </>
  );
}

function EventRow({ e }: { e: TEvent }) {
  const [open, setOpen] = useState(false);
  if (e.kind === "text") {
    const user = e.role === "user";
    return (
      <div className={`grid grid-cols-[4.5rem_1fr] gap-3 ${user ? "border-l-2 border-accent/70 bg-raised py-2 pr-3" : "py-1"}`}>
        <span className={`pt-[3px] text-right font-mono text-[10px] uppercase tracking-wider ${user ? "text-accent" : "text-faint"}`}>
          {user ? "you" : "claude"}
        </span>
        <div className={`min-w-0 whitespace-pre-wrap text-[14px] leading-6 ${user ? "text-fg" : "text-fg/90"} ${e.partial ? "opacity-80" : ""}`}>
          <Inline text={e.text} />
          {e.partial && <span className="blink ml-0.5 inline-block h-4 w-[7px] bg-dim align-middle" />}
        </div>
      </div>
    );
  }
  if (e.kind === "tool_use" || e.kind === "tool_result" || e.kind === "thinking") {
    const label =
      e.kind === "tool_use" ? `${e.tool}  ${e.activity ?? ""}` : e.kind === "thinking" ? "thinking" : e.is_error ? "tool error" : "result";
    return (
      <div className="grid grid-cols-[4.5rem_1fr] gap-3 font-mono text-[11px]">
        <span className="text-right uppercase tracking-wider text-faint">{e.kind === "tool_use" ? "tool" : ""}</span>
        <div className="min-w-0">
          <button onClick={() => setOpen(!open)} className={`truncate text-left ${e.is_error ? "text-bad" : "text-mute"} hover:text-dim`}>
            {open ? "−" : "+"} {label}
          </button>
          {open && (
            <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap border-l border-line-strong pl-3 text-[11px] text-dim">{e.text}</pre>
          )}
        </div>
      </div>
    );
  }
  const color = e.kind === "error" ? "text-bad" : e.kind === "permission" ? "text-accent" : "text-ok/80";
  return <div className={`py-1 text-center font-mono text-[11px] ${color}`}>{e.text}</div>;
}

export function TranscriptPane({
  s,
  color,
  events,
  draft,
  setDraft,
  listening,
  terminalVia = null,
  onSend,
  onClose,
  openReport = null,
  stale = false,
  onResearch,
}: {
  s: Session;
  color: string;
  events: TEvent[];
  draft: string;
  setDraft: (t: string) => void;
  listening: boolean;
  /** Terminal sessions the dashboard can type into get a message box too. */
  terminalVia?: "tmux" | "cmux" | null;
  onSend: (text: string) => void;
  onClose: () => void;
  /** The session's open decision, if it's blocked on one. */
  openReport?: Report | null;
  /** Showing the last known copy: the backend is reconnecting or no longer lists this session. */
  stale?: boolean;
  /** Research this session's product (scouts on): opens the research dialog aimed at it. */
  onResearch?: () => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [denyMsg, setDenyMsg] = useState("");
  const [follow, setFollow] = useDrawerState(s.id, "follow", true);
  const [diffPath, setDiffPath] = useDrawerState<string | null>(s.id, "diff", null);
  const [files, setFiles] = useState<FileDiff[]>([]);
  const [showTests, setShowTests] = useDrawerState(s.id, "tests", false);
  const tests = s.info?.tests ?? null;
  const canSend = !s.readonly || !!terminalVia;
  const lastText = events[events.length - 1]?.text;
  useEffect(() => {
    // braces matter: newer browsers return a Promise from scrollIntoView, and an effect may only return a cleanup
    if (follow && !diffPath && !showTests) end.current?.scrollIntoView({ block: "end" });
  }, [events.length, lastText, follow, diffPath, showTests]);

  // files touched this turn, refetched as the turn's edits change
  const turnKey = `${s.info?.tools ?? 0}:${s.info?.files.join("|") ?? ""}:${s.turn_started_at ?? 0}`;
  useEffect(() => {
    let live = true;
    api.get<{ files: FileDiff[] }>(`/api/sessions/${s.id}/turn`).then((r) => live && setFiles(r.files), () => {});
    return () => {
      live = false;
    };
  }, [s.id, turnKey]);
  const diff = diffPath ? files.find((f) => f.path === diffPath) : undefined;

  // Esc closes the diff (or the test runs) before it closes the drawer
  useEffect(() => {
    if (!diffPath && !showTests) return;
    const k = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector("[aria-modal='true']")) return;
      e.stopImmediatePropagation();
      if (diffPath) setDiffPath(null);
      else setShowTests(false);
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [diffPath, setDiffPath, showTests, setShowTests]);

  const decide = (rid: string, allow: boolean) =>
    api.post(`/api/sessions/${s.id}/permissions/${rid}`, { allow, message: allow ? null : denyMsg || null }).then(() => setDenyMsg(""));

  return (
    <section className="flex h-full min-h-0 flex-col border-l border-line bg-ground">
      <header className="flex items-center justify-between gap-2 border-b border-line px-5 py-3" style={{ boxShadow: `inset 0 3px 0 ${color}` }}>
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h2 className="truncate font-serif text-[20px]" style={{ color: color }}>{s.name}</h2>
            <StateBadge state={s.state} />
          </div>
          <div className="truncate font-mono text-[11px] text-mute">
            {tildify(s.cwd)} <span className="text-faint">/ {s.id.slice(0, 8)}</span>
            {s.info?.title && <span className="ml-2 font-sans text-dim">{s.info.title}</span>}
          </div>
          {stale && <div className="mt-0.5 font-mono text-[11px] text-accent">reconnecting: showing the last known state</div>}
        </div>
        <div className="flex shrink-0 gap-2">
          {onResearch && (
            <Button variant="ghost" onClick={onResearch} title={`Competition, market, feature or user research on ${s.name}`}>
              Research
            </Button>
          )}
          {!s.readonly && (s.state === "working" || s.state === "waiting") && (
            <Button variant="danger" onClick={() => api.post(`/api/sessions/${s.id}/interrupt`)}>
              Stop
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} title="Close (Esc)" className="font-mono text-xs">
            esc
          </Button>
        </div>
      </header>

      {/* this turn's files, and the follow toggle */}
      <div className="flex items-center gap-3 border-b border-line bg-sunk px-5 py-1.5 font-mono text-[11px]">
        <span className="shrink-0 uppercase tracking-wider text-mute">this turn</span>
        <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto py-0.5">
          {files.length === 0 && <span className="text-mute">no files edited</span>}
          {files.map((f) => {
            const on = f.path === diffPath;
            return (
              <button
                key={f.path}
                onClick={() => {
                  setShowTests(false);
                  setDiffPath(on ? null : f.path);
                }}
                aria-pressed={on}
                title={`${f.name}: show the diff`}
                className={`shrink-0 rounded-[5px] border-[1.5px] px-1.5 ${on ? "border-sun bg-raised text-fg" : "border-line-strong text-dim hover:border-dim hover:text-fg"}`}
              >
                {f.name.split("/").pop()}
              </button>
            );
          })}
        </div>
        <button
          onClick={() => {
            setDiffPath(null);
            setShowTests(!showTests);
          }}
          aria-pressed={showTests}
          title="Test runs this session made"
          className={`shrink-0 ${showTests ? "text-fg" : "text-mute hover:text-fg"}`}
        >
          tests{tests && (tests.running || tests.last) ? <>{" "}<TestChip t={tests} /></> : ""}
        </button>
        <button
          onClick={() => setFollow(!follow)}
          aria-pressed={follow}
          title={follow ? "Following the newest message. Click to stop" : "Click to follow the newest message"}
          className={`shrink-0 ${follow ? "text-work" : "text-mute hover:text-fg"}`}
        >
          {follow ? "● follow" : "○ follow"}
        </button>
      </div>

      {openReport && <PendingQuestion key={openReport.id} report={openReport} />}

      {showTests && !diff ? (
        <TestsPanel
          sid={s.id}
          summary={tests}
          onBack={() => setShowTests(false)}
          onAsk={
            canSend && s.state !== "working" && s.state !== "waiting"
              ? () => onSend("Run the project's test suite now, then tell me in one line how it went and what failed.")
              : undefined
          }
        />
      ) : diff ? (
        <div className="drafting min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <button onClick={() => setDiffPath(null)} className="font-mono text-[11px] text-mute hover:text-fg">
            ← transcript <span className="text-faint">(esc)</span>
          </button>
          <DiffView item={diff} />
        </div>
      ) : (
        <div
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget;
            const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            if (follow && !atEnd) setFollow(false);
          }}
          className="drafting min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-4"
        >
          {events.length === 0 && <p className="py-8 text-center text-[13px] text-mute">No messages yet.</p>}
          {events.map((e) => (
            <EventRow key={e.id} e={e} />
          ))}
          <div ref={end} />
        </div>
      )}

      {!openReport && s.permissions.map((p) => (
        <div key={p.request_id} className="relative mx-5 mb-3 overflow-hidden rounded-[7px] border-[1.5px] border-sun/60 bg-raised py-3 pl-4 pr-3 text-sm shadow-card">
          <span className="absolute inset-y-0 left-0 w-[3px] bg-sun" />
          <div className="text-fg">
            {s.name} wants to use <span className="font-mono">{p.tool}</span>: {p.activity}
          </div>
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-[6px] bg-sunk p-2 font-mono text-[11px] text-dim">
            {p.input}
          </pre>
          <div className="mt-2 flex gap-2">
            <Button variant="primary" onClick={() => decide(p.request_id, true)}>
              Allow
            </Button>
            <input
              value={denyMsg}
              onChange={(e) => setDenyMsg(e.target.value)}
              placeholder="Optional reply / reason for denying…"
              className="min-w-0 flex-1 rounded-[6px] border border-line-strong bg-sunk px-2 text-sm outline-none focus:border-dim"
            />
            <Button onClick={() => decide(p.request_id, false)}>Deny</Button>
          </div>
        </div>
      ))}

      {s.readonly && !terminalVia ? (
        <div className="border-t border-line px-5 py-3 text-xs text-mute">
          Terminal session, not in tmux or a cmux pane, so it can&apos;t be typed into from here. Continue it in its terminal.
        </div>
      ) : (
        <form
          className="flex items-end gap-2 border-t border-line bg-sunk px-5 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.trim()) onSend(draft.trim());
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (draft.trim()) onSend(draft.trim());
              }
            }}
            rows={2}
            placeholder={
              listening
                ? "Listening…"
                : s.readonly
                  ? `Message ${s.name} in its terminal (${terminalVia}). Hold Space outside the box to dictate; Enter sends.`
                  : `Message ${s.name}. Hold Space outside the box to talk.`
            }
            className={`min-w-0 flex-1 resize-none rounded-[6px] border bg-ground px-3 py-2 text-[14px] outline-none placeholder:text-faint ${
              listening ? "border-bad" : "border-line-strong focus:border-dim"
            }`}
          />
          <Button variant="primary" type="submit" disabled={!draft.trim()}>
            Send
          </Button>
        </form>
      )}
    </section>
  );
}
