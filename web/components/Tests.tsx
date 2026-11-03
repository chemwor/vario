"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TestRun, TestSummary } from "@/lib/types";
import { Button, fmtElapsed, useNow } from "./ui";

const TONE: Record<TestRun["status"], string> = {
  passed: "text-ok",
  failed: "text-bad",
  interrupted: "text-accent",
  unknown: "text-mute",
};

function counts(r: Pick<TestRun, "passed" | "failed" | "skipped">) {
  if (r.passed == null && r.failed == null) return null;
  const total = (r.passed ?? 0) + (r.failed ?? 0) + (r.skipped ?? 0);
  return r.failed ? `${r.failed} failed / ${total}` : `${r.passed} passed${r.skipped ? `, ${r.skipped} skipped` : ""}`;
}

/** The session row's test chip: running now, or how the last run went. */
export function TestChip({ t }: { t: TestSummary }) {
  if (t.running) {
    return (
      <span className="text-work" title={`running: ${t.running.cmd}`}>
        testing…
      </span>
    );
  }
  const r = t.last;
  if (!r) return null;
  const c = counts(r);
  const label = r.status === "passed" ? `✓ ${c ?? "tests passed"}` : r.status === "failed" ? `✗ ${c ?? "tests failed"}` : `tests ${r.status}`;
  const fails = r.failures.length ? `\nfailing: ${r.failures.join(", ")}` : "";
  return (
    <span className={TONE[r.status]} title={`last test run: ${r.cmd}${fails}`}>
      {label}
    </span>
  );
}

function RunRow({ r, open, onToggle }: { r: TestRun; open: boolean; onToggle: () => void }) {
  const now = useNow();
  const c = counts(r);
  return (
    <div className="relative overflow-hidden rounded-[7px] border-[1.5px] border-line-strong bg-raised">
      <span className={`absolute inset-y-0 left-0 w-[3px] ${r.status === "passed" ? "bg-ok" : r.status === "failed" ? "bg-bad" : "bg-mute"}`} />
      <button onClick={onToggle} className="block w-full py-2 pl-4 pr-3 text-left" aria-expanded={open}>
        <div className="flex items-baseline justify-between gap-3">
          <span className={`font-mono text-[11px] uppercase tracking-wider ${TONE[r.status]}`}>
            {r.status}
            {c && <span className="ml-2 normal-case tracking-normal">{c}</span>}
          </span>
          <span className="shrink-0 font-mono text-[10.5px] text-mute">
            {r.framework && <span className="mr-2">{r.framework}</span>}
            {fmtElapsed(Math.max(0, r.ended - r.started))} · {fmtElapsed(Math.max(0, now - r.ended))} ago
          </span>
        </div>
        <div className="mt-0.5 truncate font-mono text-[11.5px] text-dim">$ {r.cmd}</div>
      </button>
      {r.failures.length > 0 && (
        <ul className="pb-2 pl-4 pr-3 font-mono text-[11px] text-bad">
          {r.failures.slice(0, open ? 25 : 4).map((f) => (
            <li key={f} className="truncate" title={f}>
              ✗ {f}
            </li>
          ))}
          {!open && r.failures.length > 4 && <li className="text-mute">+{r.failures.length - 4} more</li>}
        </ul>
      )}
      {open && (
        <pre className="max-h-[45vh] overflow-auto whitespace-pre-wrap border-t border-line bg-sunk px-4 py-2 font-mono text-[11px] text-dim">
          {r.truncated && <span className="text-faint">… (earlier output cut)\n</span>}
          {r.output || "(no output)"}
        </pre>
      )}
    </div>
  );
}

/** Every test run the session made, newest first, with the output a click away. Vario never runs tests itself:
 * "Ask it to run the tests" sends the session a message, the same as typing it. */
export function TestsPanel({
  sid,
  summary,
  onBack,
  onAsk,
}: {
  sid: string;
  summary: TestSummary | null;
  onBack: () => void;
  onAsk?: () => void;
}) {
  const [runs, setRuns] = useState<TestRun[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const key = `${summary?.runs ?? 0}:${summary?.last?.ended ?? 0}:${summary?.running?.started ?? 0}`;
  useEffect(() => {
    let live = true;
    api.get<{ runs: TestRun[] }>(`/api/sessions/${sid}/tests`).then(
      (r) => {
        if (!live) return;
        setRuns(r.runs);
        setOpen((o) => o ?? (r.runs[0]?.status === "failed" ? r.runs[0].id : null));
      },
      () => live && setRuns([]),
    );
    return () => {
      live = false;
    };
  }, [sid, key]);

  return (
    <div className="drafting min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
      <div className="flex items-center justify-between gap-3">
        <button onClick={onBack} className="font-mono text-[11px] text-mute hover:text-fg">
          ← transcript <span className="text-faint">(esc)</span>
        </button>
        {onAsk && (
          <Button variant="ghost" onClick={onAsk} disabled={!!summary?.running} title="Sends the session a message asking it to run its tests">
            Ask it to run the tests
          </Button>
        )}
      </div>
      {summary?.running && (
        <div className="font-mono text-[11.5px] text-work">
          running now: <span className="text-dim">$ {summary.running.cmd}</span>
        </div>
      )}
      {runs === null && <p className="py-6 text-center text-[13px] text-mute">Loading…</p>}
      {runs?.length === 0 && !summary?.running && (
        <p className="py-6 text-center text-[13px] leading-6 text-mute">
          No test runs yet. When this session runs its tests (pytest, vitest, jest, go test, cargo test, npm test and
          others), the results show up here.
        </p>
      )}
      {runs?.map((r) => (
        <RunRow key={r.id} r={r} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} />
      ))}
    </div>
  );
}
