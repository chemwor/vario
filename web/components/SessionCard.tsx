"use client";
import type { Session } from "@/lib/types";
import { TestChip } from "./Tests";
import { fmtElapsed, plain, shortModel, STATE_STYLE, tildify } from "./ui";

/** The detail line: title, model, branch, context, and what this turn has touched. */
function meta(s: Session): React.ReactNode[] {
  const i = s.info;
  if (!i) return [];
  const out: React.ReactNode[] = [];
  if (i.model) out.push(shortModel(i.model));
  if (i.branch && i.branch !== "HEAD") out.push(<span title="git branch">⎇ {i.branch}</span>);
  if (i.context) out.push(<ContextMeter used={i.context} max={i.context_window} />);
  if (i.mode && i.mode !== "default" && i.mode !== "auto") out.push(i.mode); // only when unusual
  if (i.tools) out.push(`${i.tools} tool${i.tools === 1 ? "" : "s"}`);
  if (i.agents) out.push(<span className="text-work">{i.agents} subagent{i.agents === 1 ? "" : "s"}</span>);
  if (i.tests && (i.tests.running || i.tests.last)) out.push(<TestChip t={i.tests} />);
  if (i.files.length) {
    const last = i.files[i.files.length - 1];
    out.push(
      <span title={`edited this turn: ${i.files.join(", ")}`}>
        edited {last}
        {i.files.length > 1 && ` +${i.files.length - 1}`}
      </span>,
    );
  }
  if (i.cost) out.push(`$${i.cost.toFixed(2)}`);
  return out;
}

/** Context used: a short bar + percent; sun from 75%, rust from 90% (a report suggests wrapping up there). */
function ContextMeter({ used, max }: { used: number; max: number }) {
  const pct = Math.min(100, Math.round((used / (max || 200_000)) * 100));
  const tone = pct >= 90 ? "bad" : pct >= 75 ? "accent" : null;
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={`context: ${used.toLocaleString()} of ${(max || 200_000).toLocaleString()} tokens (last request)`}
      aria-label={`context ${pct} percent used`}
    >
      <span className="relative inline-block h-[5px] w-12 overflow-hidden rounded-[1px] bg-line" aria-hidden="true">
        <span
          className={`absolute inset-y-0 left-0 ${tone === "bad" ? "bg-bad" : tone === "accent" ? "bg-accent" : "bg-mute"}`}
          style={{ width: `${Math.max(pct, 2)}%` }}
        />
      </span>
      <span className={`tabular-nums ${tone === "bad" ? "text-bad" : tone === "accent" ? "text-accent" : ""}`}>{pct}%</span>
    </span>
  );
}

/** Done, qualified by how the turn went (triage labels the last message ok / partial / failed). */
function doneStyle(s: Session) {
  const o = s.info?.outcome;
  if (s.state !== "done" || !o || o.label === "ok") return null;
  return o.label === "failed"
    ? { label: "failed", text: "text-bad", dot: "bg-bad", title: o.line ?? "The turn ended without doing what was asked." }
    : { label: "done-check", text: "text-accent", dot: "bg-accent", title: o.line ?? "Partly done: check the result." };
}

/** One session as a ledger row: colour edge, name and path, what it's doing now, detail, elapsed time. */
export function SessionCard({
  s,
  color,
  now,
  selected,
  blocked = false,
  erroredSince = null,
  repeat = 1,
  onRetry,
  monitorOnly = false,
  onSelect,
  onToggleMute,
  onToggleNarrate,
}: {
  s: Session;
  color: string;
  now: number;
  selected: boolean;
  /** Waiting on you: a permission prompt or an open decision. */
  blocked?: boolean;
  /** Errored (stopped on an error or has an open error report) since this time: rust edge, age badge, Retry. */
  erroredSince?: number | null;
  /** Identical errors collapsed into the open report. */
  repeat?: number;
  onRetry?: () => void;
  /** A terminal session Vario can't type into (not in tmux / cmux): you see and hear it, and answer it there. */
  monitorOnly?: boolean;
  onSelect: () => void;
  onToggleMute: () => void;
  onToggleNarrate: () => void;
}) {
  // an open decision counts as waiting even after the turn ended (it asked a question, or errored)
  const errored = erroredSince != null;
  const outcome = doneStyle(s);
  const base = errored ? STATE_STYLE.error : blocked ? STATE_STYLE.waiting : STATE_STYLE[s.state] ?? STATE_STYLE.done;
  const st = !errored && !blocked && outcome ? { ...base, ...outcome } : base;
  const stuck = s.state === "working" && !!s.info?.stuck;
  const working = s.state === "working";
  const details = meta(s);
  const live = s.state === "working" || s.state === "waiting";
  const elapsed = live ? fmtElapsed(now - (s.turn_started_at ?? s.updated_at)) : fmtElapsed(now - s.updated_at);
  const line =
    s.permissions.length > 0 ? (
      <span className="text-accent">approve {s.permissions[0].activity}</span>
    ) : s.activity && s.state === "working" ? (
      <span className="text-dim">{s.activity}</span>
    ) : (
      <span className="text-mute">{s.snippet ? plain(s.snippet) : "No messages yet"}</span>
    );
  const toggle = (on: boolean, warn = false) =>
    `font-mono text-[10px] uppercase tracking-wider ${on ? (warn ? "text-bad" : "text-accent") : "text-faint opacity-0 group-hover:opacity-100 hover:text-dim"}`;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      className={`group relative cursor-pointer border-b border-line py-2.5 pl-4 pr-3 outline-none transition-colors focus-visible:bg-hover ${
        selected ? "bg-raised" : "hover:bg-raised/60"
      }`}
    >
      {/* hand-ruled identity stroke: a slightly uneven pen line, heavier while working or selected */}
      <svg
        className={`absolute inset-y-1.5 left-[3px] h-[calc(100%-12px)] w-[5px] ${working || selected ? "" : "opacity-65"}`}
        viewBox="0 0 5 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M2.6 0 C2 18 3.1 34 2.4 52 S2.9 84 2.3 100"
          fill="none"
          stroke={color}
          strokeWidth={working || selected ? 3 : 2.2}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {errored && <span className="absolute inset-y-0 left-0 w-[3px] bg-bad" aria-hidden="true" />}
      {working && (
        <span className="wind" style={{ color }} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      )}
      <div className="flex items-baseline gap-3">
        <span className="flex max-w-[70%] shrink-0 items-baseline gap-2 truncate text-[14px] font-medium" style={{ color }}>
          <span className={`inline-block h-2 w-2 shrink-0 self-center rounded-[1px] ${st.dot}`} />
          <span className="truncate">{s.name}</span>
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-faint" title={s.cwd}>
          {tildify(s.cwd)}
        </span>
        <span className="flex shrink-0 items-baseline gap-3">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleNarrate();
            }}
            title={s.narrate ? "Stop narrating every message" : "Narrate every message (alerts are always on)"}
            className={toggle(s.narrate)}
          >
            narrate
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleMute();
            }}
            title={s.muted ? "Unmute this session's alerts" : "Mute this session's alerts"}
            className={toggle(s.muted, true)}
          >
            {s.muted ? "muted" : "mute"}
          </button>
          {errored && onRetry && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRetry();
              }}
              onKeyDown={(e) => e.stopPropagation()}
              title="Send this session its retry message"
              className="stamp font-mono text-[10px] uppercase tracking-wider text-bad hover:text-fg focus-visible:text-fg"
            >
              retry
            </button>
          )}
          {errored && (
            <span
              className="font-mono text-[10px] tabular-nums text-bad"
              title={`erroring for ${fmtElapsed(now - erroredSince!)}${repeat > 1 ? `, same error ${repeat} times` : ""}`}
            >
              {fmtElapsed(now - erroredSince!)}
              {repeat > 1 && <span className="ml-1">×{repeat}</span>}
            </span>
          )}
          {monitorOnly && (
            <span
              className="stamp font-mono text-[9.5px] uppercase tracking-wider text-mute"
              title="Vario can show and speak this session but can't reply to it: it isn't running in tmux. Start it inside tmux (alias claude='tmux new -A -s claude-$(basename $PWD) claude') or from Vario's New session to answer it from here."
              tabIndex={0}
            >
              monitor only
            </span>
          )}
          {stuck && (
            <span className="stamp font-mono text-[9.5px] uppercase tracking-wider text-accent" title="No tool calls or results for a while (STUCK_MINUTES)">
              possibly stuck
            </span>
          )}
          <span className={`w-28 text-right text-xs ${st.text}`} title={"title" in st ? (st.title as string) : undefined}>
            {st.label}
            {!errored && <span className="ml-2 font-mono text-[11px] tabular-nums text-mute">{elapsed}</span>}
          </span>
        </span>
      </div>
      {s.info?.title && <div className="mt-0.5 truncate text-[12px] text-dim">{s.info.title}</div>}
      <div className="mt-0.5 truncate text-[13px] leading-5">{line}</div>
      {details.length > 0 && (
        <div className="mt-0.5 flex min-w-0 items-baseline gap-x-2 truncate font-mono text-[10.5px] text-mute">
          {details.map((d, k) => (
            <span key={k} className="shrink-0 whitespace-nowrap">
              {k > 0 && <span className="mr-2 text-faint">·</span>}
              {d}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
