"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Playbook, Report, Scout, Session } from "@/lib/types";
import { Button, fmtElapsed, Modal } from "./ui";

export function ScoutStrip({ scouts, now }: { scouts: Record<number, Scout>; now: number }) {
  const list = Object.values(scouts)
    .filter((s) => s.status === "running" || now - (s.finished_at ?? 0) < 3600)
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, 4);
  if (!list.length) return null;
  return (
    <div className="border-b border-line px-5 py-2 text-[11px]">
      {list.map((s) => (
        <div key={s.id} className="flex items-center gap-2 py-0.5">
          <span className={`h-1.5 w-1.5 shrink-0 ${s.status === "running" ? "blink bg-work" : s.status === "done" ? "bg-ok" : "bg-bad"}`} />
          <span className="truncate text-dim">
            <span className="text-work">scout {s.id}</span> · {s.project} · {s.brief}
          </span>
          <span className="ml-auto shrink-0 tabular-nums text-mute">
            {s.status === "running" ? fmtElapsed(now - s.created_at) : s.cost_usd != null ? `$${s.cost_usd.toFixed(2)}` : s.status}
          </span>
        </div>
      ))}
    </div>
  );
}

export function ScoutDialog({
  reports,
  sessions,
  initialSession,
  onClose,
}: {
  reports: Record<number, Report>;
  sessions: Session[];
  /** Aim the research at this session's product (the one you opened it from). */
  initialSession: string | null;
  onClose: () => void;
}) {
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [customDir, setCustomDir] = useState("");
  const [kind, setKind] = useState<string>("competition"); // or "custom"
  const [sessionId, setSessionId] = useState<string>(initialSession ?? "");
  const [focus, setFocus] = useState("");
  const [brief, setBrief] = useState("");
  const [project, setProject] = useState("");
  const [linked, setLinked] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<{ playbooks: Playbook[]; custom_dir: string }>("/api/research/playbooks").then((r) => {
      setPlaybooks(r.playbooks);
      setCustomDir(r.custom_dir);
    }, () => {});
  }, []);
  const decisions = Object.values(reports).filter((r) => r.type === "decision_needed" && (r.status === "queued" || r.status === "delivered"));
  const pb = playbooks.find((p) => p.kind === kind);
  const target = sessions.find((x) => x.id === sessionId);
  const ready = kind === "custom" ? !!brief.trim() : !!(sessionId || project.trim());
  const launch = async () => {
    setBusy(true);
    setError(null);
    try {
      if (kind === "custom") {
        const r = linked ? reports[Number(linked)] : null;
        await api.post("/api/scouts", { brief, project: project || r?.project || target?.name || null, linked_report_id: r?.id ?? null });
      } else {
        await api.post("/api/scouts", { kind, session_id: sessionId || null, project: sessionId ? null : project, focus: focus || null });
      }
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const field = "mt-1 w-full rounded-[6px] border border-line-strong bg-sunk px-2 py-1.5 text-sm";
  return (
    <Modal title="Research" onClose={onClose}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Kind of research">
        {[...playbooks, { kind: "custom", label: "Custom question", description: "Any question you want answered from the web.", budget_usd: 1, path: "", custom: false } as Playbook].map((p) => (
          <button
            key={p.kind}
            role="radio"
            aria-checked={kind === p.kind}
            onClick={() => setKind(p.kind)}
            className={`rounded-[6px] border px-3 py-2 text-left ${kind === p.kind ? "border-fg bg-hover" : "border-line-strong hover:border-dim"}`}
          >
            <span className="block text-[13px] text-fg">{p.label}</span>
            <span className="mt-0.5 block text-[11.5px] leading-snug text-mute">{p.description}</span>
          </button>
        ))}
      </div>

      {kind !== "custom" ? (
        <div className="mt-4 space-y-3">
          <label className="block text-xs text-dim">
            Product: the session working on it
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)} className={field}>
              <option value="">(no session: type a product name below)</option>
              {sessions.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                  {x.info?.title ? ` · ${x.info.title}` : ""}
                </option>
              ))}
            </select>
          </label>
          {!sessionId && (
            <label className="block text-xs text-dim">
              Product name
              <input value={project} onChange={(e) => setProject(e.target.value)} className={field} placeholder="e.g. billing-service" />
            </label>
          )}
          <label className="block text-xs text-dim">
            Focus (optional)
            <input value={focus} onChange={(e) => setFocus(e.target.value)} className={field}
              placeholder={kind === "users" ? "e.g. freelancers who invoice monthly" : kind === "features" ? "e.g. onboarding" : "e.g. EU only"} />
          </label>
          <p className="text-[11.5px] leading-snug text-mute">
            {target
              ? `The scout gets ${target.name}'s README, CLAUDE.md, package description and what the session has been asked, so it researches this product. `
              : ""}
            Web search and fetch only; budget up to ${pb?.budget_usd.toFixed(2) ?? "…"}. The report lands in the inbox
            {target ? `, attached to ${target.name},` : ""} and is kept as markdown in ~/Vario/research/.
          </p>
          {pb && (
            <p className="text-[11px] text-faint">
              {pb.custom ? `Using your playbook ${pb.path}.` : `To change how this research works, copy its playbook to ${customDir}/${pb.kind}.md and edit it.`}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-4">
          <label className="text-xs font-medium text-dim">Research brief</label>
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={3}
            placeholder="cheapest hosted rate limiter for a Node API"
            className="mt-1 w-full resize-none rounded-[6px] border border-line-strong bg-sunk px-3 py-2 text-sm outline-none focus:border-dim"
          />
          <div className="mt-3 grid grid-cols-2 gap-3">
            <label className="text-xs text-dim">
              Project
              <input value={project} onChange={(e) => setProject(e.target.value)} placeholder={target?.name ?? "(from linked decision)"} className={field} />
            </label>
            <label className="text-xs text-dim">
              Informs decision (urgency bumps when it lands)
              <select value={linked} onChange={(e) => setLinked(e.target.value)} className={field}>
                <option value="">none</option>
                {decisions.map((r) => (
                  <option key={r.id} value={r.id}>#{r.id} {r.project}: {r.summary.slice(0, 60)}</option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!ready || busy} onClick={launch}>
          {busy ? "Starting…" : "Start research"}
        </Button>
      </div>
    </Modal>
  );
}
