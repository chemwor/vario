"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RecentSession, Session } from "@/lib/types";
import { Button, fmtElapsed, Modal, useNow } from "./ui";

export function ResumeDialog({ onClose, onOpen }: { onClose: () => void; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<RecentSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = useNow();
  useEffect(() => {
    api.get<RecentSession[]>("/api/recent").then(setRows, (e) => setError(String(e)));
  }, []);

  const adopt = async (r: RecentSession) => {
    try {
      const s = await api.post<Session>("/api/sessions/adopt", { id: r.id, cwd: r.cwd });
      onOpen(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Modal title="Resume a session" onClose={onClose}>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      {!rows ? (
        <p className="text-sm text-mute">Loading…</p>
      ) : (
        <ul className="max-h-[60vh] divide-y divide-line overflow-y-auto">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-fg">{r.name}</span>
                  {r.owned && <span className="rounded bg-accent/10 px-1.5 text-[10px] uppercase text-accent">dashboard</span>}
                  <span className="text-[11px] text-mute">{fmtElapsed(now - r.last_modified)} ago</span>
                </div>
                <div className="truncate text-xs text-dim">{r.title}</div>
              </div>
              {r.owned ? (
                <Button onClick={() => onOpen(r.id)}>Open</Button>
              ) : (
                <Button
                  disabled={!r.idle}
                  title={r.idle ? "Take over this session in the dashboard (resumes via the SDK)" : "Still active in a terminal"}
                  onClick={() => adopt(r)}
                >
                  {r.idle ? "Adopt" : "In use"}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
