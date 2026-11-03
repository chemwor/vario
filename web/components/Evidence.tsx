"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Evidence, EvidenceItem, Report } from "@/lib/types";
import { Button, fmtElapsed, plain } from "./ui";

const STATE_NOTE: Record<string, string> = {
  pending: "awaiting approval",
  applied: "applied",
  "not in file now": "not in the file now (refused or changed later)",
  snippet: "file not readable; edit only",
  "new file": "new file",
  written: "written in full",
  migration: "migration SQL (applied)",
};

/** Unified diff in ink: additions on a grass wash, removals on rust, hunk headers in sky. */
export function DiffView({ item }: { item: Extract<EvidenceItem, { kind: "diff" }> }) {
  const lines = item.diff.split("\n");
  return (
    <figure className="overflow-hidden rounded-[6px] border-[1.5px] border-line bg-raised">
      <figcaption className="flex items-baseline gap-2 border-b border-line px-3 py-1.5 font-mono text-[11px]">
        <span className="min-w-0 flex-1 truncate text-fg" title={item.path}>
          {item.name}
        </span>
        {item.edits && item.edits > 1 && <span className="text-mute">{item.edits} edits</span>}
        <span className={item.state === "pending" ? "text-accent" : "text-mute"}>{STATE_NOTE[item.state] ?? item.state}</span>
      </figcaption>
      <div className="max-h-[26rem] overflow-auto">
        <pre className="min-w-max py-1 font-mono text-[11.5px] leading-[1.55]">
          {lines.map((l, i) => {
            const cls = l.startsWith("+++") || l.startsWith("---")
              ? "text-mute"
              : l.startsWith("+")
                ? "bg-ok/12 text-fg"
                : l.startsWith("-")
                  ? "bg-bad/12 text-fg"
                  : l.startsWith("@@")
                    ? "text-work"
                    : "text-dim";
            return (
              <div key={i} className={`px-3 ${cls}`}>
                {l || " "}
              </div>
            );
          })}
        </pre>
      </div>
      {item.truncated && <p className="border-t border-line px-3 py-1 text-[11px] text-mute">Diff cut at 600 lines.</p>}
    </figure>
  );
}

function Item({ it }: { it: EvidenceItem }) {
  switch (it.kind) {
    case "diff":
      return <DiffView item={it} />;
    case "command":
      return (
        <div className="rounded-[6px] border-[1.5px] border-line bg-raised">
          <div className="border-b border-line px-3 py-1.5 font-mono text-[11px] text-mute">
            {it.description ? `${it.tool}: ${it.description}` : it.tool}
          </div>
          <pre className="overflow-x-auto whitespace-pre-wrap break-all px-3 py-2 font-mono text-[12px] leading-[1.55] text-fg">
            <span className="select-none text-mute">$ </span>
            {it.command}
          </pre>
        </div>
      );
    case "input":
      return (
        <div className="rounded-[6px] border-[1.5px] border-line bg-raised">
          <div className="border-b border-line px-3 py-1.5 font-mono text-[11px] text-mute">{it.tool}</div>
          <pre className="max-h-72 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-[1.55] text-dim">{it.text}</pre>
        </div>
      );
    case "sources":
      return (
        <ol className="space-y-1.5 text-[13px]">
          {it.sources.map((s, i) => (
            <li key={s.url} className="flex gap-2">
              <span className="font-mono text-[11px] tabular-nums text-mute">{String(i + 1).padStart(2, "0")}</span>
              <a href={s.url} target="_blank" rel="noreferrer noopener" className="min-w-0 text-work underline decoration-work/40 underline-offset-4 hover:decoration-work">
                {s.title}
                {s.title !== s.url && <span className="block truncate font-mono text-[10.5px] text-mute no-underline">{s.url}</span>}
              </a>
            </li>
          ))}
        </ol>
      );
    case "lines":
      return (
        <ol className="radio-log rounded-[6px] border-[1.5px] border-line bg-raised px-3 py-2 font-mono text-[11.5px] leading-6 text-dim">
          {it.lines.map((l, i) => (
            <li key={i} className="truncate" title={l}>
              {l}
            </li>
          ))}
        </ol>
      );
    case "text":
      return (
        <div className="rounded-[6px] border-[1.5px] border-line bg-raised px-3 py-2">
          <div className="font-mono text-[11px] uppercase tracking-wider text-mute">{it.title}</div>
          <p className="mt-1 whitespace-pre-wrap text-[13px] text-fg">{it.text}</p>
        </div>
      );
  }
}

/** The evidence list for one report, or a plain statement that there isn't any. */
export function EvidenceList({ ev, loading }: { ev: Evidence | null; loading: boolean }) {
  if (loading && !ev) return <p className="text-[13px] text-mute">Reading the transcript…</p>;
  if (!ev) return <p className="text-[13px] text-bad">Couldn&apos;t load the evidence.</p>;
  return (
    <div className="space-y-3">
      {ev.items.map((it, i) => (
        <Item key={i} it={it} />
      ))}
      {ev.note && <p className="rounded-[6px] border-[1.5px] border-dashed border-line-strong px-3 py-2 text-[13px] text-dim">{ev.note}</p>}
    </div>
  );
}

export function useEvidence(reportId: number | null) {
  const [got, setGot] = useState<{ key: string; ev: Evidence | null } | null>(null);
  const [nonce, setNonce] = useState(0);
  const key = `${reportId}:${nonce}`;
  useEffect(() => {
    if (reportId === null) return;
    let live = true;
    api.get<Evidence>(`/api/reports/${reportId}/evidence`).then(
      (ev) => live && setGot({ key, ev }),
      () => live && setGot({ key, ev: null }),
    );
    return () => {
      live = false;
    };
  }, [reportId, key]);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ev: got?.ev ?? null, loading: got?.key !== key, reload };
}

/** Approve / Deny / reply for one report. Everything goes through /api/voice/utterance, the same pipeline as
    the mic, pinned to this report: Approve sends "yes", Deny "no", with any typed text as the instruction. */
export function ReplyBar({ report }: { report: Report }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const decision = report.type === "decision_needed";
  const post = async (utterance: string) => {
    setBusy(true);
    setSaid(null);
    try {
      const res = await api.post<{ say?: string; cancelled?: boolean }>("/api/voice/utterance", {
        text: utterance,
        source: "panel",
        report_id: report.id,
        selected_session_id: report.session_id,
      });
      setSaid(res.cancelled ? "Cancelled." : res.say || "Sent.");
      setText("");
    } catch (e) {
      setSaid(e instanceof Error ? e.message : "Failed.");
    } finally {
      setBusy(false);
    }
  };
  const t = text.trim();
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (t) post(t);
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        {decision && (
          <>
            <Button type="button" variant="primary" disabled={busy} onClick={() => post(t ? `yes, ${t}` : "approve")}>
              Approve
            </Button>
            <Button type="button" variant="danger" disabled={busy} onClick={() => post(t ? `no, ${t}` : "deny")}>
              Deny
            </Button>
          </>
        )}
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label={decision ? "Instruction to send with your answer, or a reply" : "Reply"}
          placeholder={decision ? "Optional: with conditions, or a reply…" : "Reply…"}
          className="h-8 min-w-[10rem] flex-1 rounded-[6px] border-[1.5px] border-line-strong bg-ground px-2.5 text-[13px] outline-none placeholder:text-faint focus:border-sun"
        />
        <Button type="submit" disabled={busy || !t}>
          Send
        </Button>
      </div>
      {said && <p className="font-mono text-[11px] text-dim" role="status">{said}</p>}
    </form>
  );
}

const TYPE_LABEL = { decision_needed: "decision", research: "research", fyi: "fyi" } as const;

/** Evidence panel: the question, what it's about, and the answer controls. */
export function ReportPanel({
  report,
  now,
  onClose,
  onOpenSession,
}: {
  report: Report;
  now: number;
  onClose: () => void;
  onOpenSession: (id: string) => void;
}) {
  const { ev, loading, reload } = useEvidence(report.id);
  const open = report.status === "queued" || report.status === "delivered";
  const urgent = report.type === "decision_needed" && report.urgency >= 4;
  return (
    <section className="flex h-full min-h-0 flex-col border-l-[1.5px] border-line bg-ground" aria-label="Report evidence">
      <header className="border-b-[1.5px] border-line px-5 py-3">
        <div className="flex items-center gap-2.5 font-mono text-[10px] uppercase tracking-wider">
          <span className={`stamp ${urgent ? "text-bad" : report.type === "decision_needed" ? "text-accent" : report.type === "research" ? "text-work" : "text-mute"}`}>
            {TYPE_LABEL[report.type]}
          </span>
          <span className="font-sans text-xs normal-case tracking-normal text-dim">{report.project}</span>
          <span className="normal-case tabular-nums text-mute">{fmtElapsed(now - report.created_at)} ago</span>
          {!open && <span className="normal-case text-mute">· {report.status}</span>}
          <span className="ml-auto flex gap-1">
            {report.session_id && (
              <Button variant="ghost" className="h-7 text-xs" onClick={() => onOpenSession(report.session_id!)}>
                Open session
              </Button>
            )}
            <Button variant="ghost" onClick={onClose} title="Close (Esc)" className="h-7 font-mono text-xs">
              esc
            </Button>
          </span>
        </div>
        <h2 className="mt-2 font-serif text-[19px] leading-snug text-fg">{plain(report.summary)}</h2>
        {open && <div className="mt-3"><ReplyBar report={report} /></div>}
        {!open && report.decision_text && <p className="mt-2 text-[13px] text-dim">Answered: {report.decision_text}</p>}
      </header>
      <div className="drafting min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-mute">Evidence</h3>
          <button onClick={reload} className="font-mono text-[11px] text-mute hover:text-fg">
            refresh
          </button>
        </div>
        <EvidenceList ev={ev} loading={loading} />
        {report.body && report.body !== report.summary && (
          <details className="text-[13px]">
            <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-[0.14em] text-mute hover:text-fg">
              Full report text
            </summary>
            <pre className="mt-2 whitespace-pre-wrap border-l-2 border-line-strong pl-3 font-sans leading-6 text-dim">{report.body}</pre>
          </details>
        )}
      </div>
    </section>
  );
}
