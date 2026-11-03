"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface MusicState {
  mode: "pause" | "duck" | "off";
  resume: boolean;
  playing: string[];
}

/** What happens to Spotify / Apple Music when an alert or briefing needs your attention. */
export function MusicControl() {
  const [m, setM] = useState<MusicState | null>(null);
  useEffect(() => {
    api.get<MusicState>("/api/music").then(setM, () => {});
  }, []);
  const update = (patch: Partial<MusicState>) => api.post<MusicState>("/api/music", patch).then(setM);
  if (!m) return null;
  return (
    <span className="flex items-center gap-2" title="When Vario speaks to you (alerts, briefing)">
      <select
        value={m.mode}
        onChange={(e) => update({ mode: e.target.value as MusicState["mode"] })}
        className="cursor-pointer appearance-none bg-transparent text-dim outline-none hover:text-fg"
      >
        <option value="pause">pause music</option>
        <option value="duck">lower music</option>
        <option value="off">leave music</option>
      </select>
      <label className="flex cursor-pointer items-center gap-1 hover:text-fg">
        <input type="checkbox" checked={m.resume} onChange={(e) => update({ resume: e.target.checked })} />
        resume
      </label>
    </span>
  );
}
