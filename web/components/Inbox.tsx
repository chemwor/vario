"use client";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { CommsEntry, Report, Scout } from "@/lib/types";
import { Comms } from "./Comms";
import { ScoutStrip } from "./Scouts";
import { fmtElapsed, plain } from "./ui";

const TYPE_STYLE = {
  decision_needed: { label: "decision", cls: "text-accent" },
  research: { label: "research", cls: "text-work" },
  fyi: { label: "fyi", cls: "text-mute" },
} as const;

/** Same order as the backend: decision_needed first, then urgency (high first), then age (oldest first). */
export function sortReports(list: Report[]) {
  return list.sort(
    (a, b) =>
      Number(b.type === "decision_needed") - Number(a.type === "decision_needed") ||
      b.urgency - a.urgency ||
      a.created_at - b.created_at,
  );
}

function Urgency({ n }: { n: number }) {
  return (
    <span className="flex items-end gap-[2px]" title={`urgency ${n}/5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={`w-[3px] ${i <= n ? (n >= 4 ? "bg-bad" : "bg-dim") : "bg-line-strong"}`} style={{ height: 4 + i * 1.5 }} />
      ))}
    </span>
  );
}

export function Inbox({
  reports,
  scouts,
  comms,
  showScouts = true,
  now,
  onOpenSession,
  selectedReport,
  onSelectReport,
}: {
  reports: Record<number, Report>;
  scouts: Record<number, Scout>;
  comms: Record<number, CommsEntry>;
  showScouts?: boolean;
  now: number;
  onOpenSession: (id: string) => void;
  selectedReport: number | null;
  onSelectReport: (id: number | null) => void;
}) {
  const [sent, setSent] = useState<Record<number, string>>({});
  const [pane, setPane] = useState<"inbox" | "comms">("inbox");
  const [project, setProject] = useState<string | null>(null);
  const open = useMemo(() => sortReports(Object.values(reports).filter((r) => r.status === "queued" || r.status === "delivered")), [reports]);
  const counts = useMemo(() => {
    const c: Record<string, { total: number; decisions: number }> = {};
    for (const r of open) {
      c[r.project] ??= { total: 0, decisions: 0 };
      c[r.project].total++;
      if (r.type === "decision_needed") c[r.project].decisions++;
    }
    return Object.entries(c).sort((a, b) => b[1].decisions - a[1].decisions || b[1].total - a[1].total);
  }, [open]);
  const shown = project ? open.filter((r) => r.project === project) : open;
  const act = (r: Report, status: "answered" | "skipped") => api.post(`/api/reports/${r.id}`, { status });

  const decisions = open.filter((r) => r.type === "decision_needed").length;
  const commsFlagged = useMemo(() => Object.values(comms).filter((e) => e.flagged && !e.corrected_by).length, [comms]);
  const tab = (on: boolean) => `border-b pb-1 text-xs transition-colors ${on ? "border-fg text-fg" : "border-transparent text-mute hover:text-dim"}`;

  return (
    <aside className="flex min-h-0 w-[24rem] shrink-0 flex-col border-r-[1.5px] border-line bg-sunk">
      <div className={`border-b border-line px-5 pt-4 ${pane === "comms" ? "pb-3" : ""}`}>
        <div className="flex items-baseline gap-5">
          {(["inbox", "comms"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPane(p)}
              className={`font-serif text-[20px] transition-colors ${pane === p ? "text-fg" : "text-faint hover:text-dim"}`}
            >
              {p === "inbox" ? "Inbox" : "Comms"}
              {p === "comms" && commsFlagged > 0 && (
                <span className="ml-1 align-top font-mono text-[10px] text-bad">{commsFlagged}</span>
              )}
            </button>
          ))}
          {pane === "inbox" && (
            <span className="ml-auto font-mono text-[11px] text-mute">
              {open.length} open
              {decisions > 0 && <span className="text-accent"> / {decisions} decision{decisions === 1 ? "" : "s"}</span>}
            </span>
          )}
        </div>
        {pane === "inbox" && (
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
            <button onClick={() => setProject(null)} className={tab(project === null)}>
              All
            </button>
            {counts.map(([p, c]) => (
              <button key={p} onClick={() => setProject(project === p ? null : p)} className={tab(project === p)}>
                {p}
                <span className={`ml-1.5 font-mono text-[10px] ${c.decisions ? "text-accent" : "text-faint"}`}>{c.total}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {pane === "comms" ? (
        <Comms entries={comms} />
      ) : (
        <>
          {showScouts && <ScoutStrip scouts={scouts} now={now} />}
          <ol className="drafting min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
            {shown.length === 0 && (
              <li className="px-5 py-10 text-center text-[13px] text-mute">Nothing waiting on you.</li>
            )}
            {shown.map((r, i) => {
              const t = TYPE_STYLE[r.type];
              // research about a session's product: hand the report to that session
              const research = r.type === "research" ? Object.values(scouts).find((x) => x.report_id === r.id && x.library_path && x.session_id) : undefined;
              return (
                <li
                  key={r.id}
                  className={`inked relative overflow-hidden px-4 py-3 ${r.id === selectedReport ? "ring-2 ring-sun ring-offset-1 ring-offset-sunk" : ""}`}
                >
                  {r.type === "decision_needed" && <span className={`absolute inset-y-0 left-0 w-[3px] ${r.urgency >= 4 ? "bg-bad" : "bg-sun"}`} />}
                  {/* index card: the ruled header line under the stamp row */}
                  <div className="flex items-center gap-2.5 border-b border-[color-mix(in_oklab,var(--rust)_30%,transparent)] pb-1.5 font-mono text-[10px] uppercase tracking-wider">
                    <span className="tabular-nums text-faint">{String(i + 1).padStart(2, "0")}</span>
                    <span className={`stamp ${r.type === "decision_needed" && r.urgency >= 4 ? "text-bad" : t.cls}`}>{t.label}</span>
                    {r.repeat > 1 && (
                      <span className="stamp tabular-nums text-bad" title={`the same error ${r.repeat} times`}>
                        ×{r.repeat}
                      </span>
                    )}
                    {r.outcome && r.outcome !== "ok" && (
                      <span className={`stamp ${r.outcome === "failed" ? "text-bad" : "text-accent"}`} title={r.outcome_line ?? undefined}>
                        {r.outcome === "failed" ? "failed" : "partial"}
                      </span>
                    )}
                    <Urgency n={r.urgency} />
                    <span className="truncate font-sans text-xs normal-case tracking-normal text-dim">{r.project}</span>
                    <span className="ml-auto shrink-0 normal-case tabular-nums text-mute">{fmtElapsed(now - r.created_at)}</span>
                  </div>
                  <button
                    className="mt-1.5 block w-full text-left text-[14px] leading-[1.45] text-fg"
                    aria-pressed={r.id === selectedReport}
                    title="Show the evidence"
                    onClick={() => onSelectReport(r.id === selectedReport ? null : r.id)}
                  >
                    {plain(r.summary)}
                  </button>
                  {sent[r.id] && <p className="mt-1.5 text-[11.5px] text-dim" role="status">{sent[r.id]}</p>}
                  <div className="mt-2 flex items-center gap-3 text-[11px] text-mute">
                    <span>
                      {r.source.replace("_", " ")}
                      {r.status === "delivered" ? " / spoken" : ""}
                      {!r.triaged ? " / triaging" : ""}
                    </span>
                    <span className="ml-auto flex gap-3">
                      {research && (
                        <button
                          className="font-medium text-work hover:text-fg"
                          title={`Tell the session to read ${research.library_path}`}
                          onClick={() =>
                            api.post<{ say: string }>(`/api/scouts/${research.id}/handoff`, {}).then(
                              (res) => setSent((m) => ({ ...m, [r.id]: res.say })),
                              (e) => setSent((m) => ({ ...m, [r.id]: String(e) })),
                            )
                          }
                        >
                          {sent[r.id] ? "Sent" : "Send to session"}
                        </button>
                      )}
                      {r.session_id && (r.kind === "error" || r.kind === "fail") && (
                        <button
                          className="font-medium text-bad hover:text-fg"
                          title="Send the session its retry message"
                          onClick={() => api.post(`/api/sessions/${r.session_id}/retry`, {}).catch(() => {})}
                        >
                          Retry
                        </button>
                      )}
                      {r.session_id && (
                        <button className="hover:text-fg" onClick={() => onOpenSession(r.session_id!)}>
                          Open
                        </button>
                      )}
                      <button className="hover:text-fg" onClick={() => act(r, "skipped")}>
                        Skip
                      </button>
                      <button className="hover:text-ok" onClick={() => act(r, "answered")}>
                        Done
                      </button>
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </aside>
  );
}
