"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";
import { Button, Modal } from "./ui";

interface DirList {
  path: string;
  parent: string;
  dirs: string[];
}

export function NewSessionDialog({
  onClose,
  onCreated,
  prompt,
  setPrompt,
  listening,
  onMic,
}: {
  onClose: () => void;
  onCreated: (s: Session) => void;
  prompt: string;
  setPrompt: (t: string) => void;
  listening: boolean;
  onMic: (down: boolean) => void;
}) {
  const [cwd, setCwd] = useState("");
  const [projects, setProjects] = useState<{ cwd: string; name: string }[]>([]);
  const [browse, setBrowse] = useState<DirList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ cwd: string; name: string }[]>("/api/projects").then(setProjects, () => {});
  }, []);

  const open = (path: string) => api.get<DirList>(`/api/dirs?path=${encodeURIComponent(path)}`).then(setBrowse, (e) => setError(String(e)));

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await api.post<Session>("/api/sessions", { cwd, prompt: prompt.trim() || null });
      onCreated(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="New session" onClose={onClose}>
      <label className="text-xs font-medium text-dim">Project directory</label>
      <div className="mt-1 flex gap-2">
        <input
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          placeholder="~/code/my-project"
          className="min-w-0 flex-1 rounded-[6px] border border-line-strong bg-sunk px-3 py-2 font-mono text-sm outline-none focus:border-dim"
        />
        <Button onClick={() => open(cwd || "~")}>Browse</Button>
      </div>

      {browse ? (
        <div className="mt-2 max-h-48 overflow-y-auto rounded-[6px] border border-line bg-sunk text-sm">
          <div className="sticky top-0 flex items-center justify-between bg-raised px-3 py-1.5 font-mono text-xs text-dim">
            <span className="truncate">{browse.path}</span>
            <span className="flex gap-2">
              <button className="hover:text-fg" onClick={() => open(browse.parent)}>
                up
              </button>
              <button className="text-accent hover:text-fg" onClick={() => (setCwd(browse.path), setBrowse(null))}>
                use this
              </button>
            </span>
          </div>
          {browse.dirs.map((d) => (
            <button
              key={d}
              className="block w-full truncate px-3 py-1 text-left hover:bg-hover"
              onClick={() => open(`${browse.path}/${d}`)}
              onDoubleClick={() => (setCwd(`${browse.path}/${d}`), setBrowse(null))}
            >
              <span className="text-faint">/</span>{d}
            </button>
          ))}
        </div>
      ) : (
        projects.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {projects.slice(0, 14).map((p) => (
              <button
                key={p.cwd}
                title={p.cwd}
                onClick={() => setCwd(p.cwd)}
                className={`rounded-[6px] border px-2.5 py-0.5 text-xs ${
                  cwd === p.cwd ? "border-fg text-fg" : "border-line-strong text-dim hover:text-fg"
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
        )
      )}

      <label className="mt-4 block text-xs font-medium text-dim">Initial prompt</label>
      <div className="mt-1 flex gap-2">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          placeholder={listening ? "Listening…" : "What should Claude do? (or hold the mic button to speak)"}
          className={`min-w-0 flex-1 resize-none rounded-[6px] border bg-sunk px-3 py-2 text-sm outline-none ${
            listening ? "border-bad" : "border-line-strong focus:border-dim"
          }`}
        />
        <button
          onPointerDown={() => onMic(true)}
          onPointerUp={() => onMic(false)}
          onPointerLeave={() => listening && onMic(false)}
          className={`w-16 shrink-0 rounded-[6px] border font-mono text-[10px] uppercase tracking-wider ${listening ? "border-bad bg-bad/15 text-bad" : "border-line-strong text-dim hover:border-dim hover:text-fg"}`}
          title="Hold to speak"
        >
          {listening ? "rec" : "hold"}
        </button>
      </div>
      <p className="mt-1 text-[11px] text-mute">Runs with permission mode acceptEdits; other tools ask for approval in the dashboard.</p>

      {error && <p className="mt-3 text-sm text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" disabled={!cwd.trim() || busy} onClick={create}>
          {busy ? "Starting…" : "Start session"}
        </Button>
      </div>
    </Modal>
  );
}
