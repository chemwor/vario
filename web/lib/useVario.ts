"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, WS_URL } from "./api";
import type { AwakeState, BriefingState, CommsEntry, PendingState, Profile, Report, ReplySent, Scout, Session, TEvent, VoiceState, WsEvent } from "./types";

const MAX_EVENTS = 600;

export interface Caption {
  text: string;
  session_id: string | null;
  at: number;
  delivered: boolean;
}

export function useVario(onSelectRequest?: (id: string) => void) {
  const [sessions, setSessions] = useState<Record<string, Session>>({});
  const [events, setEvents] = useState<Record<string, TEvent[]>>({});
  const [voice, setVoice] = useState<VoiceState | null>(null);
  const [connected, setConnected] = useState(false);
  const [caption, setCaption] = useState<Caption | null>(null);
  const [reports, setReports] = useState<Record<number, Report>>({});
  const [briefing, setBriefing] = useState<BriefingState | null>(null);
  const [replies, setReplies] = useState<ReplySent[]>([]);
  const [scouts, setScouts] = useState<Record<number, Scout>>({});
  const [awake, setAwake] = useState<AwakeState | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [comms, setComms] = useState<Record<number, CommsEntry>>({});
  const [pending, setPending] = useState<PendingState | null>(null);
  const [lastDone, setLastDone] = useState<{ id: number; outcome: "dispatch" | "cancel"; why: string; at: number } | null>(null);
  const loaded = useRef<Set<string>>(new Set());
  const selectRef = useRef(onSelectRequest);
  useEffect(() => {
    selectRef.current = onSelectRequest;
  });

  const pushEvent = useCallback((sid: string, fn: (list: TEvent[]) => TEvent[]) => {
    setEvents((prev) => ({ ...prev, [sid]: fn(prev[sid] ?? []).slice(-MAX_EVENTS) }));
  }, []);

  const handle = useCallback(
    (m: WsEvent) => {
      switch (m.type) {
        case "snapshot":
          setSessions(Object.fromEntries(m.sessions.map((s) => [s.id, s])));
          setVoice(m.voice);
          setBriefing(m.briefing);
          setAwake(m.awake);
          break;
        case "session_state":
          setSessions((p) => ({ ...p, [m.session.id]: m.session }));
          break;
        case "session_removed":
          setSessions((p) => {
            const rest = { ...p };
            delete rest[m.session_id];
            return rest;
          });
          break;
        case "message_delta":
          if (!loaded.current.has(m.session_id)) break; // history is fetched on open
          if (m.final) {
            pushEvent(m.session_id, (l) => {
              const without = m.replaces ? l.filter((e) => e.id !== m.replaces) : l;
              return without.some((e) => e.id === m.event.id) ? without : [...without, m.event];
            });
          } else {
            pushEvent(m.session_id, (l) => {
              const i = l.findIndex((e) => e.id === m.key);
              if (i === -1)
                return [...l, { id: m.key, ts: Date.now() / 1000, role: "assistant", kind: "text", text: m.delta, partial: true }];
              const copy = l.slice();
              copy[i] = { ...copy[i], text: copy[i].text + m.delta };
              return copy;
            });
          }
          break;
        case "tool_activity":
          setSessions((p) => (p[m.session_id] ? { ...p, [m.session_id]: { ...p[m.session_id], activity: m.activity } } : p));
          break;
        case "speak":
          setCaption({ text: m.text, session_id: m.session_id, at: Date.now(), delivered: m.delivered });
          break;
        case "tts_status":
          setVoice(m.voice);
          break;
        case "briefing_state":
          setBriefing(m.briefing);
          break;
        case "reply_sent": {
          const { type: _t, ...reply } = m;
          void _t;
          setReplies((p) => [reply, ...p].slice(0, 20));
          break;
        }
        case "awake":
          setAwake(m.awake);
          break;
        case "scout":
          setScouts((p) => ({ ...p, [m.scout.id]: m.scout }));
          break;
        case "report":
          setReports((p) => ({ ...p, [m.report.id]: m.report }));
          break;
        case "comms":
          setComms((p) => ({ ...p, [m.entry.id]: m.entry }));
          break;
        case "pending":
          setPending(m.pending);
          break;
        case "pending_done":
          setPending((p) => (p && p.id === m.id ? null : p));
          setLastDone({ id: m.id, outcome: m.outcome, why: m.why, at: Date.now() });
          break;
      }
    },
    [pushEvent],
  );

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    let closed = false;
    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        setConnected(true);
        api.get<{ reports: Report[] }>("/api/reports").then(
          (r) => setReports(Object.fromEntries(r.reports.map((x) => [x.id, x]))),
          () => {},
        );
        api.get<Profile>("/api/profile").then(setProfile, () => {});
        api.get<{ entries: CommsEntry[]; pending: PendingState | null }>("/api/comms").then((r) => {
          setComms(Object.fromEntries(r.entries.map((x) => [x.id, x])));
          setPending(r.pending);
        }, () => {});
        api.get<Scout[]>("/api/scouts").then((l) => setScouts(Object.fromEntries(l.map((x) => [x.id, x]))), () => {});
        // re-sync open transcripts after a reconnect
        for (const sid of loaded.current) {
          api.get<TEvent[]>(`/api/sessions/${sid}/events`).then((ev) => setEvents((p) => ({ ...p, [sid]: ev })), () => {});
        }
      };
      ws.onmessage = (e) => handle(JSON.parse(e.data));
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [handle]);

  const openSession = useCallback(async (sid: string) => {
    loaded.current.add(sid);
    try {
      const ev = await api.get<TEvent[]>(`/api/sessions/${sid}/events`);
      setEvents((p) => ({ ...p, [sid]: ev }));
    } catch {
      /* session may have disappeared */
    }
  }, []);

  const runCommand = useCallback(async (text: string) => {
    const res = await api.post<{ action?: string; session_id?: string; say?: string | null; command?: string | null }>(
      "/api/command",
      { text },
    );
    if (res.action === "select" && res.session_id) selectRef.current?.(res.session_id);
    return res;
  }, []);

  return { sessions, events, voice, connected, caption, reports, briefing, replies, scouts, awake, profile, comms, pending, lastDone,
    openSession, runCommand };
}
