"use client";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Button, Modal } from "./ui";

interface Entry {
  ts: number;
  project: string | null;
  report_id: number | null;
  report_summary: string | null;
  report_type: string | null;
  said: string | null;
  action: string;
  routed_to: string | null;
  result: string | null;
  via: "voice" | "terminal" | "session" | "dashboard";
  heard?: string | null; // whisper's transcript, when you corrected it
}

const ACTION_CLS: Record<string, string> = {
  approve: "text-ok", deny: "text-bad", reply: "text-work", note: "text-accent",
  scout: "text-work", spawn: "text-work", later: "text-accent", skipped: "text-mute", skip: "text-mute",
};

function where(r: string | null) {
  if (!r) return "—";
  if (r.startsWith("cmux:")) return `terminal (${r})`;
  if (r.startsWith("tmux:")) return `terminal (${r})`;
  if (r.startsWith("scout:")) return r.replace("scout:", "scout #");
  if (r === "decision_log") return "decision log";
  if (r.length === 36) return `session ${r.slice(0, 8)}`;
  return r;
}

function toMarkdown(rows: Entry[]) {
  const byDay: Record<string, Entry[]> = {};
  for (const r of rows) (byDay[new Date(r.ts * 1000).toDateString()] ??= []).push(r);
  return Object.entries(byDay)
    .map(
      ([day, list]) =>
        `## ${day}\n\n` +
        list
          .slice()
          .reverse()
          .map(
            (r) =>
              `- ${new Date(r.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} **${r.project ?? "—"}**` +
              (r.report_summary ? ` — ${r.report_summary}` : "") +
              `\n  - said: “${r.said ?? ""}”${r.heard ? ` (heard “${r.heard}”)` : ""} → **${r.action}** → ${where(r.routed_to)}${r.result ? ` (${r.result})` : ""}`,
          )
          .join("\n"),
    )
    .join("\n\n");
}

/** The daily record: what you decided, what you said, and where it went. */
export function DecisionLog({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState(1);
  const [project, setProject] = useState<string>("");
  const [rows, setRows] = useState<Entry[] | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    api.get<Entry[]>(`/api/decision-record?days=${days}`).then(setRows, () => setRows([]));
  }, [days]);
  const projects = useMemo(() => [...new Set((rows ?? []).map((r) => r.project).filter(Boolean))] as string[], [rows]);
  const shown = (rows ?? []).filter((r) => !project || r.project === project).filter((r) => r.action !== "next");

  return (
    <Modal title="Decision log" onClose={onClose}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        {[1, 7, 30].map((d) => (
          <button key={d} onClick={() => setDays(d)}
            className={`rounded-[6px] border px-2.5 py-0.5 ${days === d ? "border-fg text-fg" : "border-line-strong text-dim"}`}>
            {d === 1 ? "today" : `${d} days`}
          </button>
        ))}
        <select value={project} onChange={(e) => setProject(e.target.value)}
          className="rounded border border-line-strong bg-raised px-1.5 py-0.5 text-fg">
          <option value="">all projects</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <span className="text-mute">{shown.length} entries</span>
        <Button className="ml-auto" onClick={() => navigator.clipboard?.writeText(toMarkdown(shown)).then(() => setCopied(true))}>
          {copied ? "Copied" : "Copy as Markdown"}
        </Button>
      </div>
      <ol className="max-h-[60vh] divide-y divide-line overflow-y-auto">
        {rows === null && <li className="py-6 text-center text-sm text-mute">Loading…</li>}
        {rows !== null && shown.length === 0 && <li className="py-6 text-center text-sm text-mute">No decisions in this period.</li>}
        {shown.map((r, i) => (
          <li key={i} className="py-2.5 text-sm">
            <div className="flex items-center gap-2 text-[11px] text-mute">
              <span className="tabular-nums">{new Date(r.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
              <span className="font-medium text-dim">{r.project ?? "—"}</span>
              <span className="rounded bg-hover px-1">{r.via}</span>
              {r.report_type && <span>{r.report_type.replace("_", " ")}</span>}
            </div>
            {r.report_summary && <div className="mt-0.5 text-dim">{r.report_summary}</div>}
            <div className="mt-0.5 text-fg">
              <span className="text-mute">said </span>“{r.said ?? "—"}”
              <span className={`ml-2 font-semibold ${ACTION_CLS[r.action] ?? "text-dim"}`}>→ {r.action}</span>
              <span className="text-mute"> → {where(r.routed_to)}</span>
            </div>
            {r.heard && (
              <div className="text-[11px] text-mute">
                heard <span className="line-through decoration-bad/60">“{r.heard}”</span>, corrected
              </div>
            )}
            {r.result && <div className="text-[11px] text-mute">{r.result}</div>}
          </li>
        ))}
      </ol>
    </Modal>
  );
}
