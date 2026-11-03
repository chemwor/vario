"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BriefingBanner, MicPill } from "@/components/BriefingBar";
import { DecisionLog } from "@/components/DecisionLog";
import { DevHarness } from "@/components/DevHarness";
import { Inbox } from "@/components/Inbox";
import { MusicControl } from "@/components/MusicControl";
import { ScoutDialog } from "@/components/Scouts";
import { NewSessionDialog } from "@/components/NewSessionDialog";
import { ResumeDialog } from "@/components/ResumeDialog";
import { SessionCard } from "@/components/SessionCard";
import { Health } from "@/components/Health";
import { SetupWizard, type SetupState } from "@/components/SetupWizard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TranscriptPane } from "@/components/TranscriptPane";
import { ReportPanel } from "@/components/Evidence";
import { assignColors, Button, Label, useNow } from "@/components/ui";
import { api } from "@/lib/api";
import { playChime } from "@/lib/chime";
import type { Report, Session } from "@/lib/types";
import { useVario } from "@/lib/useVario";
import { useFocus } from "@/lib/focusStore";
import { VarioMark } from "@/components/VarioMark";
import { type SttMeta, usePushToTalk } from "@/lib/usePushToTalk";
import { CaptionBar } from "@/components/CaptionBar";


export default function Vario() {
  // right-hand slot: a session's drawer or a report's evidence panel (survives reconnects; see focusStore)
  const { focus, setSession: setSelected, setReport: selectReport, clear: clearFocus } = useFocus();
  const selected = focus.session;
  const selectedReport = focus.report;
  const [dialog, setDialog] = useState<"new" | "resume" | "scout" | "log" | "health" | "setup" | null>(null);
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [researchFor, setResearchFor] = useState<string | null>(null);
  const loadSetup = useCallback(
    () =>
      api.get<SetupState>("/api/setup").then((s) => {
        setSetup(s);
        return s;
      }),
    [],
  );
  // first run (no config, no hooks, no setup record) or #setup: open the wizard
  useEffect(() => {
    loadSetup().then(
      (s) => {
        const h = window.location.hash;
        if (h === "#health") setDialog("health");
        else if (s.first_run || h.startsWith("#setup")) setDialog("setup");
      },
      () => {},
    );
  }, [loadSetup]);
  const [draft, setDraft] = useState("");
  const [command, setCommand] = useState("");
  const [newPrompt, setNewPrompt] = useState("");
  const [lastCommand, setLastCommand] = useState<string | null>(null);
  const [harness, setHarness] = useState(false);
  const now = useNow();

  const mc = useVario(setSelected);
  const { sessions, events, voice, connected, caption, reports, briefing, replies, scouts, awake, profile, comms, pending, lastDone, openSession } =
    mc;
  const scoutsOn = profile?.scouts ?? false;   // hidden until the profile says they're allowed
  const [interim, setInterim] = useState("");
  const armed = !!briefing?.armed;
  const live = selected ? sessions[selected] : undefined;
  // last known copy, so the drawer stays put while the backend reconnects or re-scans terminals
  const [kept, setKept] = useState<Session | null>(null);
  if (live && live !== kept) setKept(live);
  const current = live ?? (kept && kept.id === selected ? kept : undefined);
  const stale = !!current && (!live || !connected);
  const openReportFor = useMemo(() => {
    const m: Record<string, Report> = {};
    for (const r of Object.values(reports)) {
      if (!r.session_id || r.type !== "decision_needed" || (r.status !== "queued" && r.status !== "delivered")) continue;
      if (!m[r.session_id] || r.created_at < m[r.session_id].created_at) m[r.session_id] = r;
    }
    return m;
  }, [reports]);
  const report = selectedReport !== null ? reports[selectedReport] : undefined;

  useEffect(() => {
    if (selected) openSession(selected);
  }, [selected, openSession]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && !dialog && clearFocus();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [dialog, clearFocus]);

  const send = useCallback(
    async (sid: string, text: string) => {
      setDraft("");
      await api.post(`/api/sessions/${sid}/messages`, { text });
    },
    [],
  );

  // Terminal sessions: can the dashboard type into them (tmux / cmux)? Checked when one is selected.
  const [termReach, setTermReach] = useState<{ id: string; via: "tmux" | "cmux" | null } | null>(null);
  const termId = current?.readonly ? current.id : null;
  useEffect(() => {
    if (!termId) return;
    let live = true;
    api
      .get<{ via: "tmux" | "cmux" | null }>(`/api/terminal/${termId}/reach`)
      .then((r) => live && setTermReach({ id: termId, via: r.via }))
      .catch(() => live && setTermReach({ id: termId, via: null }));
    return () => {
      live = false;
    };
  }, [termId]);
  const termVia = termId && termReach?.id === termId ? termReach.via : null;
  const sendTerminal = useCallback(async (sid: string, text: string) => {
    try {
      const res = await api.post<{ ok: boolean; say: string }>(`/api/terminal/${sid}/messages`, { text });
      if (res.ok) setDraft("");
      setLastCommand(res.say);
    } catch (e) {
      setLastCommand(String(e));
    }
  }, []);


  // Where does speech go? Everything except new-session dictation goes to /api/voice/utterance,
  // the same endpoint as the dev harness; the server decides briefing vs reply vs command.
  // Terminal sessions get dictation into their message box (you review it, Enter sends it).
  const target: "new" | "briefing" | "session" | "terminal" | "command" =
    dialog === "new" ? "new" : armed ? "briefing" : current && !current.readonly ? "session"
      : current && termVia ? "terminal" : "command";

  const postUtterance = useCallback(
    async (text: string, source: "mic" | "ptt", meta?: SttMeta, duringHold?: number) => {
      setInterim("");
      setDraft("");
      setCommand("");
      try {
        const res = await api.post<{ say?: string | null; action?: string | null; reason?: string }>("/api/voice/utterance", {
          text,
          source,
          selected_session_id: current && !current.readonly ? current.id : null,
          confidence: meta?.confidence ?? null,
          stt_ms: meta?.ms ?? null,
          during_hold: duringHold ?? null,
        });
        setLastCommand(res.say ?? res.reason ?? (res.action ?? null));
      } catch (e) {
        setLastCommand(String(e));
      }
    },
    [current],
  );

  // While a confirm card is up after a spoken utterance, the mic stays open so "no" / "cancel" can stop it.
  // Whatever it hears goes straight to the utterance endpoint, never into a draft.
  const holdListen = useRef<number | null>(null);
  const pendingRef = useRef(pending);
  useEffect(() => {
    pendingRef.current = pending;
  });
  const listenForHold = useRef<(id: number) => void>(() => {});
  const ptt = usePushToTalk(
    (t) => {
      if (holdListen.current !== null) return;
      setInterim(t);
      if (target === "new") setNewPrompt(t);
      else if (target === "session" || target === "terminal") setDraft(t);
      else if (target === "command") setCommand(t);
    },
    (t, meta) => {
      if (holdListen.current !== null) {
        const id = holdListen.current;
        holdListen.current = null;
        // if that didn't settle the card (a cough, a rejected mumble), keep listening for "confirm" / "cancel"
        return void postUtterance(t, "mic", meta, id).then(() => {
          if (pendingRef.current?.id === id && holdListen.current === null) listenForHold.current(id);
        });
      }
      if (target === "new") setNewPrompt(t);
      else if (target === "terminal") setDraft(t);
      else postUtterance(t, armed ? "mic" : "ptt", meta);
    },
    // Mic closed after 5s of silence: in briefing, move on now instead of waiting out the window.
    () => {
      if (holdListen.current !== null) {
        holdListen.current = null; // nothing said during the hold: it dispatches on its own
        return;
      }
      if (armed && briefing?.phase === "listening") api.post("/api/briefing/silence").catch(() => {});
    },
    profile ? (profile.stt.mode === "local" ? "local" : "webspeech") : null,
  );

  // Briefing: chime + open the mic when the server opens a listen window; close it when the window ends.
  // Depends only on the phase and the stable start/stop callbacks: re-renders (the clock ticks every
  // second) must not cancel the pending mic start.
  const lastPhase = useRef<string | null>(null);
  const listenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { start: pttStart, stop: pttStop } = ptt;
  useEffect(() => {
    const phase = briefing?.armed ? briefing.phase : "off";
    if (phase === lastPhase.current) return;
    lastPhase.current = phase;
    if (listenTimer.current) clearTimeout(listenTimer.current);
    listenTimer.current = null;
    if (phase === "listening") {
      playChime();
      listenTimer.current = setTimeout(() => {
        listenTimer.current = null;
        pttStart({ autoStopOnFinal: true });
      }, 250);
      return;
    }
    if (holdListen.current === null) pttStop(); // the confirm card's mic outlives the listen window
  }, [briefing?.armed, briefing?.phase, pttStart, pttStop]);

  // Confirm card for something you said: open the mic for the hold so "no" or "cancel" can stop it.
  // Starting to talk keeps the card up until what you said is transcribed.
  const pendingId = pending?.id ?? null;
  // (the mic waits until Athena has read it back, so it doesn't hear her)
  const pendingVoice =
    pending && !pending.reading ? pending.source === "mic" || pending.source === "ptt" || pending.source === "correction" : false;
  useEffect(() => {
    listenForHold.current = (id: number) => {
      holdListen.current = id;
      pttStart({
        autoStopOnFinal: true,
        endOfSpeechMs: 700,
        // nothing said: the mic closes as the card sends
        // listen for the whole hold: an explicit one waits (up to EXPLICIT_HOLD_S) for "confirm" or a click
        silenceMs: Math.max(800, (pendingRef.current?.deadline ?? 0) * 1000 - Date.now()) + 400,
        onVoice: () => api.post(`/api/voice/pending/${id}/extend`).catch(() => {}),
      });
    };
  }, [pttStart]);
  useEffect(() => {
    if (pendingId === null || !pendingVoice) return;
    const id = pendingId;
    const t = setTimeout(() => listenForHold.current(id), 150);
    return () => clearTimeout(t);
  }, [pendingId, pendingVoice]);
  useEffect(() => {
    if (lastDone && holdListen.current === lastDone.id) {
      holdListen.current = null;
      pttStop();
    }
  }, [lastDone, pttStop]);

  // Esc cancels the confirm card before it does anything else (closing a session, a dialog)
  useEffect(() => {
    if (pendingId === null) return;
    const k = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopImmediatePropagation();
      api.post(`/api/voice/pending/${pendingId}/cancel`).catch(() => {});
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [pendingId]);
  useEffect(() => () => {
    if (listenTimer.current) clearTimeout(listenTimer.current);
  }, []);

  const toggleBriefing = useCallback(() => api.post("/api/briefing", { value: !armed }), [armed]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleBriefing();
      }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [toggleBriefing]);

  // blocked: a permission prompt is up, or the session has an open decision (a question, an error)
  const blockedSince = useCallback(
    (s: Session) => Math.min(openReportFor[s.id]?.created_at ?? Infinity, s.state === "waiting" ? s.updated_at : Infinity),
    [openReportFor],
  );
  // errored: the session stopped on an error, or has an open error report; since when (oldest first)
  const erroredSince = useCallback(
    (s: Session) => {
      const r = openReportFor[s.id];
      const fromReport = r && (r.kind === "error" || r.kind === "fail") ? r.created_at : Infinity;
      return Math.min(fromReport, s.state === "error" ? s.updated_at : Infinity);
    },
    [openReportFor],
  );
  const rank = useCallback(
    (s: Session) =>
      erroredSince(s) < Infinity ? 0 : blockedSince(s) < Infinity ? 1 : s.state === "working" ? 2 : 3,
    [erroredSince, blockedSince],
  );
  const list = useMemo(
    () =>
      Object.values(sessions).sort(
        // attention order: error (oldest first) > blocked (longest-waiting first) > working > done (most recent first)
        (a, b) =>
          rank(a) - rank(b) ||
          (rank(a) === 0 ? erroredSince(a) - erroredSince(b) : rank(a) === 1 ? blockedSince(a) - blockedSince(b) : b.updated_at - a.updated_at),
      ),
    [sessions, rank, blockedSince, erroredSince],
  );
  const colors = useMemo(() => assignColors(Object.values(sessions)), [sessions]);
  // terminal sessions Vario can type into (tmux / cmux); the rest are monitor-only. Checked once per session.
  const [reach, setReach] = useState<Record<string, string | null>>({});
  const termIds = Object.values(sessions)
    .filter((s) => s.source === "terminal")
    .map((s) => s.id)
    .join(",");
  useEffect(() => {
    for (const id of termIds ? termIds.split(",") : []) {
      if (id in reach) continue;
      api.get<{ via: string | null }>(`/api/terminal/${id}/reach`).then(
        (r) => setReach((m) => ({ ...m, [id]: r.via })),
        () => setReach((m) => ({ ...m, [id]: null })),
      );
    }
  }, [termIds, reach]);
  const mine = list.filter((s) => s.source === "dashboard");
  const terminal = list.filter((s) => s.source === "terminal");

  const retry = (s: Session) =>
    api.post<{ ok: boolean; say: string }>(`/api/sessions/${s.id}/retry`, {}).then(
      (r) => setLastCommand(r.say),
      () => setLastCommand(`Couldn't retry ${s.name}.`),
    );
  const toggleMute = (s: Session) => api.post(`/api/sessions/${s.id}/mute`, { value: !s.muted });

  const grid = (items: Session[]) => (
    <div className="border-t border-line">
      {items.map((s) => (
        <SessionCard
          key={s.id}
          s={s}
          color={colors[s.id]}
          now={now}
          selected={s.id === selected}
          blocked={rank(s) <= 1}
          erroredSince={rank(s) === 0 ? erroredSince(s) : null}
          repeat={openReportFor[s.id]?.repeat ?? 1}
          onRetry={() => retry(s)}
          monitorOnly={s.source === "terminal" && s.id in reach && reach[s.id] === null}
          onSelect={() => setSelected(s.id === selected ? null : s.id)}
          onToggleMute={() => toggleMute(s)}
          onToggleNarrate={() => api.post(`/api/sessions/${s.id}/narrate`, { value: !s.narrate })}
        />
      ))}
    </div>
  );

  const showCaption = caption && now * 1000 - caption.at < 15000;
  const talkTo = target === "session" || target === "terminal" ? current?.name : target;
  const sep = <span className="h-4 w-px bg-line-strong" />;
  const stat = "flex items-center gap-1.5 hover:text-fg";

  return (
    <div className="flex h-screen flex-col bg-ground text-fg">
      {profile?.demo && (
        <div className="stamp shrink-0 bg-accent px-4 py-1 text-center font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-ground" role="status">
          Demo · made-up sessions and reports · your real data isn&rsquo;t here · vario demo --stop
        </div>
      )}
      {/* top bar */}
      <header data-armed={armed} className="app-header flex h-14 shrink-0 items-center gap-3 border-b-[1.5px] border-line px-5">
        <h1 className="mr-3 flex items-center gap-2">
          <VarioMark className="h-[22px] w-[22px] text-fg" />
          <span className="font-serif text-[23px] leading-none tracking-tight">Vario</span>
        </h1>
        <Button variant="primary" onClick={() => setDialog("new")}>
          New session
        </Button>
        <Button variant="ghost" onClick={() => setDialog("resume")}>Resume</Button>
        {scoutsOn && <Button variant="ghost" onClick={() => setDialog("scout")}>Scout</Button>}
        <Button variant="ghost" onClick={() => setDialog("log")}>Decisions</Button>
        <Button variant="ghost" onClick={() => (loadSetup().catch(() => {}), setDialog("health"))} title="Setup warnings and system checks">
          Health
          {(setup?.warnings.length ?? 0) > 0 && (
            <span className="font-mono text-[11px] text-accent" aria-label={`${setup!.warnings.length} warnings`}>
              !{setup!.warnings.length}
            </span>
          )}
        </Button>

        <form
          className="mx-2 flex min-w-[12rem] flex-1 items-baseline gap-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (command.trim()) postUtterance(command.trim(), "ptt");
          }}
        >
          <label htmlFor="vario-command" className="shrink-0 font-mono text-[10px] uppercase tracking-[0.16em] text-mute">
            Command
          </label>
          <input
            id="vario-command"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder={
              ptt.listening && target === "command"
                ? "Listening…"
                : '"switch to billing", "mute webapp", "what\'s the status"'
            }
            className={`h-8 min-w-0 flex-1 border-0 border-b-[1.5px] bg-transparent px-1 text-[14px] outline-none placeholder:text-faint ${
              ptt.listening && target === "command" ? "border-bad" : "border-line-strong focus:border-sun"
            }`}
          />
        </form>

        <button
          onPointerDown={() => ptt.start()}
          onPointerUp={ptt.stop}
          onPointerLeave={() => ptt.listening && ptt.stop()}
          disabled={!ptt.supported}
          title={ptt.supported ? "Hold to talk (or hold Space)" : "Speech input unavailable in this browser"}
          className={`flex h-8 items-center gap-2 rounded-[6px] border px-3 text-[13px] transition-colors disabled:opacity-40 ${
            ptt.listening
              ? "border-bad bg-bad/15 text-fg"
              : armed
                ? "border-sun bg-raised text-fg shadow-[0_0_0_3px_color-mix(in_oklab,var(--sun)_30%,transparent)]"
                : "border-line-strong text-dim hover:border-dim hover:text-fg"
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${ptt.listening ? "blink bg-bad" : "bg-faint"}`} />
          {ptt.listening ? `To ${talkTo}` : <>Talk <kbd className="font-mono text-[11px] text-mute">space</kbd></>}
        </button>

        <div className={`flex h-8 items-stretch overflow-hidden rounded-[6px] border ${armed ? "border-sun" : "border-line-strong"}`}>
          <button
            onClick={toggleBriefing}
            title="Arm/disarm briefing mode (⌘⇧B)"
            className={`flex items-center gap-2 px-3 text-[13px] font-medium transition-colors ${
              armed ? "bg-sun text-on-sun" : "text-dim hover:text-fg"
            }`}
          >
            {armed && <span className="blink h-1.5 w-1.5 rounded-full bg-on-sun" />}
            {armed ? "Briefing on" : "Briefing"}
          </button>
          <label
            title="Turn briefing on by itself when a new report is queued (while this dashboard is open)"
            className={`flex cursor-pointer items-center gap-1.5 border-l px-2.5 text-xs ${armed ? "border-sun bg-raised text-dim" : "border-line-strong text-mute"}`}
          >
            <input
              type="checkbox"
              checked={briefing?.auto ?? false}
              onChange={(e) => api.post("/api/briefing/auto", { value: e.target.checked })}
            />
            auto
          </label>
        </div>
        <ThemeToggle />
      </header>

      {armed && briefing && <BriefingBanner b={briefing} now={now} interim={interim} />}
      {(showCaption || lastCommand || ptt.error) && (
        <div className="flex flex-wrap gap-x-6 gap-y-1 border-b border-line bg-sunk px-5 py-1.5 text-xs">
          {showCaption && (
            <span className={caption!.delivered ? "text-dim" : "text-mute line-through"}>
              <span className="mr-2 font-mono text-[10px] uppercase tracking-wider text-faint">said</span>
              {caption!.text}
            </span>
          )}
          {lastCommand && (
            <span className="text-fg">
              <span className="mr-2 font-mono text-[10px] uppercase tracking-wider text-faint">result</span>
              {lastCommand}
            </span>
          )}
          {ptt.error && <span className="text-bad">{ptt.error}</span>}
        </div>
      )}

      <main className="flex min-h-0 flex-1">
        <Inbox
          reports={reports}
          scouts={scouts}
          comms={comms}
          showScouts={scoutsOn}
          now={now}
          onOpenSession={(id) => setSelected(id)}
          selectedReport={selectedReport}
          onSelectReport={selectReport}
        />
        <div className={`drafting min-h-0 overflow-y-auto pb-6 ${current || report ? "w-[22rem] shrink-0 2xl:w-[30rem]" : "flex-1"}`}>
          {!connected && Object.keys(sessions).length === 0 && (
            <p className="px-5 py-10 text-center text-[13px] text-mute">Connecting to the Vario backend… If this stays, run `vario status`.</p>
          )}
          {mine.length > 0 ? (
            <>
              <div className="flex items-baseline justify-between px-5 pb-2 pt-4">
                <Label>Dashboard sessions</Label>
                <span className="font-mono text-[11px] text-faint">{mine.length}</span>
              </div>
              {grid(mine)}
            </>
          ) : (
            connected && (
              // empty: one line, so the terminal sessions get the room
              <div className="flex items-baseline justify-between border-b border-line px-5 pb-2 pt-4">
                <Label>Dashboard sessions</Label>
                <span className="text-[12px] text-mute">
                  none ·{" "}
                  <button className="text-dim underline decoration-line-strong underline-offset-4 hover:text-fg" onClick={() => setDialog("new")}>
                    Start one
                  </button>
                </span>
              </div>
            )
          )}
          {terminal.length > 0 && (
            <>
              <div className={`flex items-baseline justify-between px-5 pb-2 ${mine.length ? "pt-7" : "pt-4"}`}>
                <Label>Terminal sessions</Label>
                <span className="font-mono text-[11px] text-faint">{terminal.length}</span>
              </div>
              {grid(terminal)}
            </>
          )}
        </div>
        {current && (
          <div className="min-h-0 min-w-0 flex-1">
            <TranscriptPane
              s={current}
              color={colors[current.id] ?? "var(--sess-0)"}
              events={events[current.id] ?? []}
              openReport={openReportFor[current.id] ?? null}
              stale={stale}
              draft={draft}
              setDraft={setDraft}
              listening={ptt.listening && (target === "session" || target === "terminal")}
              terminalVia={termVia}
              onSend={(t) => (current.readonly ? sendTerminal(current.id, t) : send(current.id, t))}
              onClose={() => setSelected(null)}
              onResearch={scoutsOn ? () => (setResearchFor(current.id), setDialog("scout")) : undefined}
            />
          </div>
        )}
        {!current && report && (
          <div className="min-h-0 min-w-0 flex-1">
            <ReportPanel key={report.id} report={report} now={now} onClose={() => selectReport(null)} onOpenSession={(id) => setSelected(id)} />
          </div>
        )}
      </main>

      <CaptionBar
        pending={pending}
        lastDone={lastDone}
        engine={
          profile
            ? profile.stt.mode === "local"
              ? `whisper ${profile.stt.model.split("/").pop()?.replace(/^whisper-|-mlx$/g, "")} · on this Mac`
              : "web speech"
            : ""
        }
        onCancel={(id) => api.post(`/api/voice/pending/${id}/cancel`).catch(() => {})}
        onSendNow={(id) => api.post(`/api/voice/pending/${id}/dispatch`).catch(() => {})}
      />

      {harness && (
        <DevHarness
          selectedSessionId={current && !current.readonly ? current.id : null}
          replies={replies}
          micLog={ptt.micLog}
          micSupported={ptt.supported}
          comms={comms}
          onClose={() => setHarness(false)}
        />
      )}

      {/* status bar */}
      <footer className="blueprint flex h-7 shrink-0 items-center gap-4 px-5 font-mono text-[11px] text-mute">
        <span className={`flex items-center gap-1.5 ${connected ? "" : "text-bad"}`} title={connected ? "API connected" : "API disconnected"}>
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-ok" : "bg-bad"}`} />
          {connected ? "connected" : "offline"}
        </span>
        {sep}
        <MicPill b={briefing} listening={ptt.listening} now={now} />
        {profile && <span title={profile.stt.model}>stt: {profile.stt.mode === "local" ? profile.stt.engine : "web speech"}</span>}
        {sep}
        <button
          className={`${stat} ${voice?.global_muted ? "text-bad" : ""}`}
          onClick={() => api.post("/api/voice/mute", { value: !voice?.global_muted })}
          title="Global mute (same as claude-mute / ~/.claude/hooks/.muted)"
        >
          voice: {voice?.global_muted ? "muted" : "on"}
        </button>
        <span className={voice?.tts_online ? "" : "text-bad"} title="Bubo, the local TTS daemon">
          bubo: {voice?.tts_online ? "online" : "offline"}
        </span>
        {sep}
        <span className="flex items-center gap-2">
          music: <MusicControl />
        </span>
        {sep}
        <span
          className={awake?.holding ? "text-accent" : ""}
          title={awake?.holding ? `Keeping the Mac awake: ${awake.reasons.join(", ")}` : "Mac may sleep (nothing active)"}
        >
          mac: {awake?.holding ? "kept awake" : "may sleep"}
        </span>
        <button className={`ml-auto ${stat} ${harness ? "text-fg" : ""}`} onClick={() => setHarness(!harness)} title="Type an utterance through the same pipeline as the mic">
          harness
        </button>
      </footer>

      {dialog === "new" && (
        <NewSessionDialog
          onClose={() => setDialog(null)}
          onCreated={(s) => {
            setDialog(null);
            setNewPrompt("");
            setSelected(s.id);
          }}
          prompt={newPrompt}
          setPrompt={setNewPrompt}
          listening={ptt.listening}
          onMic={(down) => (down ? ptt.start() : ptt.stop())}
        />
      )}
      {dialog === "scout" && scoutsOn && (
        <ScoutDialog
          reports={reports}
          sessions={list}
          initialSession={researchFor ?? selected ?? null}
          onClose={() => (setDialog(null), setResearchFor(null))}
        />
      )}
      {dialog === "log" && <DecisionLog onClose={() => setDialog(null)} />}
      {dialog === "health" && <Health setup={setup} onClose={() => setDialog(null)} onOpenSetup={() => setDialog("setup")} />}
      {dialog === "setup" && (
        <SetupWizard
          engine={profile ? (profile.stt.mode === "local" ? "local" : "webspeech") : null}
          initialStep={typeof window !== "undefined" ? window.location.hash.split("/")[1] : undefined}
          onClose={() => {
            setDialog(null);
            if (window.location.hash.startsWith("#setup")) history.replaceState(null, "", window.location.pathname);
            loadSetup().catch(() => {});
          }}
        />
      )}
      {dialog === "resume" && (
        <ResumeDialog
          onClose={() => setDialog(null)}
          onOpen={(id) => {
            setDialog(null);
            setSelected(id);
          }}
        />
      )}
    </div>
  );
}
