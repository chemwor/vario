"use client";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { API, api } from "./api";
import { setLive } from "./captionStore";
import { startLocalRecorder } from "./localRecorder";

/** "local": on-device whisper via the backend (work profile; Web Speech is never touched).
 *  "webspeech": the browser's speech service. null: profile not loaded yet, so the mic stays off. */
export type SttEngine = "local" | "webspeech" | null;

/* Minimal typing for the (prefixed) Web Speech API */
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onstart?: (() => void) | null;
  onaudiostart?: (() => void) | null;
  onspeechstart?: (() => void) | null;
}

export interface MicEvent {
  t: number;
  event: string;
  detail?: string;
}

/** Mic diagnostics: kept on screen and mirrored to the backend log (POST /api/voice/mic-debug). */
function report(event: string, detail?: string) {
  fetch(API + "/api/voice/mic-debug", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ event, detail: detail ?? null, ua: navigator.userAgent }),
  }).catch(() => {});
}

function getRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as (new () => SpeechRecognitionLike) | null;
}

/**
 * Hold-to-talk speech recognition. `onInterim` streams the live transcript; `onFinal` fires once
 * after release with the full utterance.
 */
/** Music pauses while the mic records and comes back afterwards (backend decides, per the music setting). */
function micMusic(on: boolean) {
  api.post("/api/music/mic", { on }).catch(() => {});
}

/** The mic closes on its own after this long without hearing anything new (counted from when it's live). */
export const SILENCE_STOP_MS = 5000;
/** Hands-free (briefing): after a finished phrase, wait this long for more before closing, so a short
 *  pause mid-answer doesn't cut you off. */
export const END_OF_ANSWER_MS = 1800;
/** Live caption: re-transcribe the audio so far this often while you talk. */
export const PARTIAL_MS = 700;

/** What came with a transcript: whisper's confidence and how long it took (local engine only). */
export interface SttMeta {
  confidence: number | null;
  ms: number | null;
}

export interface StartOpts {
  autoStopOnFinal?: boolean; // hands-free: stop once you've finished a phrase
  endOfSpeechMs?: number; // hands-free: how long a pause ends it (default END_OF_ANSWER_MS)
  silenceMs?: number; // give up after this long without speech (default SILENCE_STOP_MS)
  onVoice?: () => void; // you started talking
}

interface LocalDeps {
  opts: StartOpts;
  trace: (event: string, detail?: string) => void;
  setError: (e: string | null) => void;
  setListening: (v: boolean) => void;
  rec: { current: { stop(): void } | null };
  cb: { current: { onInterim: (t: string) => void; onFinal: (t: string, meta: SttMeta) => void; onNoSpeech?: () => void } };
}

/** One local-whisper recording: capture -> WAV -> /api/voice/transcribe -> onFinal (same path as Web Speech).
 *  While you talk, the audio so far is re-transcribed every PARTIAL_MS for the live caption. */
function runLocal({ opts, trace, setError, setListening, rec, cb }: LocalDeps) {
  if (!navigator.mediaDevices?.getUserMedia) {
    setError("This browser has no microphone API");
    trace("unsupported", "no getUserMedia");
    return;
  }
  trace("start", "local whisper");
  const state: { cancelled: boolean; done: boolean; inflight: boolean; live: { stop(): void } | null } = {
    cancelled: false,
    done: false,
    inflight: false,
    live: null,
  };
  rec.current = {
    stop: () => {
      state.cancelled = true;
      state.live?.stop();
    },
  };
  setError(null);
  setListening(true);
  setLive({ phase: "listening", text: "", final: false, level: 0, voiced: false, confidence: null });
  micMusic(true);
  const closed = () => {
    rec.current = null;
    setListening(false);
    micMusic(false);
  };
  startLocalRecorder({
    silenceMs: opts.silenceMs ?? SILENCE_STOP_MS,
    endOfSpeechMs: opts.autoStopOnFinal ? opts.endOfSpeechMs ?? END_OF_ANSWER_MS : undefined,
    onVoice: () => {
      trace("speech");
      opts.onVoice?.();
    },
    onLevel: (level, voiced) => setLive({ level, voiced }),
    partialMs: PARTIAL_MS,
    onPartial: (wav) => {
      if (state.inflight || state.done) return; // one partial at a time; skip frames instead of queueing
      state.inflight = true;
      transcribe(wav, true).then(
        (out) => {
          state.inflight = false;
          if (state.done || !out.text) return;
          cb.current.onInterim(out.text);
          setLive({ text: out.text });
        },
        () => {
          state.inflight = false;
        },
      );
    },
    onError: (msg, code) => {
      closed();
      trace("error", code);
      setError(msg);
      setLive({ phase: "idle", level: null, voiced: false });
    },
    onDone: (wav, reason) => {
      state.done = true;
      closed();
      trace("end", wav ? `${reason}, transcribing locally` : `${reason}, nothing heard`);
      if (!wav) {
        setLive({ phase: "idle", level: null, voiced: false, text: "" });
        return cb.current.onNoSpeech?.();
      }
      setLive({ phase: "processing", level: null, voiced: false });
      transcribe(wav).then(
        (out) => {
          trace("transcript", `${out.ms} ms: ${out.text.slice(0, 80) || "(empty)"}`);
          setLive({ phase: "idle", text: out.text, final: true, confidence: out.confidence });
          cb.current.onInterim(out.text);
          if (out.text) cb.current.onFinal(out.text, { confidence: out.confidence, ms: out.ms });
          else cb.current.onNoSpeech?.();
        },
        (e) => {
          trace("error", String(e));
          setError(`Local transcription failed: ${e instanceof Error ? e.message : e}`);
          setLive({ phase: "idle", text: "" });
          cb.current.onInterim("");
          cb.current.onNoSpeech?.();
        },
      );
    },
  }).then((r) => {
    state.live = r;
    if (r) trace("onstart");
    if (r && state.cancelled) r.stop();
  });
}

async function transcribe(wav: Blob, partial = false): Promise<{ text: string; ms: number; confidence: number | null }> {
  const res = await fetch(API + "/api/voice/transcribe" + (partial ? "?partial=true" : ""), {
    method: "POST",
    headers: { "content-type": "audio/wav" },
    body: wav,
  });
  const out = await res.json();
  if (!res.ok) throw new Error(out.detail ?? res.statusText);
  return { text: String(out.text ?? "").trim(), ms: out.ms, confidence: out.confidence ?? null };
}

export function usePushToTalk(
  onInterim: (t: string) => void,
  onFinal: (t: string, meta: SttMeta) => void,
  onNoSpeech?: () => void,
  engine: SttEngine = "webspeech",
) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [micLog, setMicLog] = useState<MicEvent[]>([]);
  const trace = useCallback((event: string, detail?: string) => {
    setMicLog((l) => [{ t: Date.now(), event, detail }, ...l].slice(0, 12));
    report(event, detail);
  }, []);
  const hasWebSpeech = useSyncExternalStore(
    () => () => {},
    () => getRecognitionCtor() !== null,
    () => true,
  );
  const hasMediaMic = useSyncExternalStore(
    () => () => {},
    () => !!navigator.mediaDevices?.getUserMedia,
    () => true,
  );
  const supported = engine === "local" ? hasMediaMic : engine === "webspeech" ? hasWebSpeech : false;
  const engineRef = useRef(engine);
  useEffect(() => {
    engineRef.current = engine;
  });
  const rec = useRef<{ stop(): void } | null>(null);
  const finalText = useRef("");
  const interimText = useRef("");
  const cb = useRef({ onInterim, onFinal, onNoSpeech });
  useEffect(() => {
    cb.current = { onInterim, onFinal, onNoSpeech };
  });
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const autoStop = useRef(false);

  /** autoStopOnFinal: hands-free mode, stop as soon as one final phrase is recognized. */
  const start = useCallback((opts?: StartOpts) => {
    if (rec.current) {
      trace("start-ignored", "already listening");
      return;
    }
    if (engineRef.current === null) {
      setError("Mic is waiting for the profile to load");
      trace("start-ignored", "profile not loaded");
      return;
    }
    if (engineRef.current === "local")
      return runLocal({ opts: opts ?? {}, trace, setError, setListening, rec, cb });
    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      setError("Speech recognition needs Chrome or Edge");
      trace("unsupported", "no SpeechRecognition in this browser");
      return;
    }
    trace("start", (Ctor as { name?: string }).name);
    const r = new Ctor();
    r.continuous = true;
    r.interimResults = true;
    r.lang = "en-US";
    finalText.current = "";
    interimText.current = "";
    autoStop.current = !!opts?.autoStopOnFinal;
    const quiet = opts?.silenceMs ?? SILENCE_STOP_MS;
    const endMs = opts?.endOfSpeechMs ?? END_OF_ANSWER_MS;
    const armSilence = (ms = quiet, why = `${quiet / 1000}s pause`) => {
      if (silenceTimer.current) clearTimeout(silenceTimer.current);
      silenceTimer.current = setTimeout(() => {
        trace("silence", `${why}, stopping`);
        r.stop();
      }, ms);
    };
    // A Bluetooth headset can take seconds to switch into mic mode: give up only if it never comes
    // up; the 5s silence clock starts once audio is actually flowing.
    armSilence(15000, "mic never started (15s)");
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) finalText.current += res[0].transcript;
        else interim += res[0].transcript;
      }
      interimText.current = interim;
      // hands-free: a finished phrase ends the answer only if nothing more follows shortly
      if (autoStop.current && finalText.current.trim() && !interim) armSilence(endMs, "end of answer");
      else armSilence();
      cb.current.onInterim((finalText.current + interim).trim());
      setLive({ text: (finalText.current + interim).trim(), voiced: !!interim });
      trace("result", (finalText.current + interim).trim().slice(0, 80));
    };
    r.onerror = (e) => {
      trace("error", e.error);
      if (e.error === "not-allowed" || e.error === "service-not-allowed")
        setError(`Microphone blocked: allow mic access for ${location.host} in the browser's site settings`);
      else if (e.error === "network") setError("Speech service unreachable (Chrome needs internet for Web Speech)");
      else if (e.error === "audio-capture") setError("No microphone found");
      else if (e.error !== "aborted" && e.error !== "no-speech") setError(`Mic error: ${e.error}`);
    };
    r.onstart = () => trace("onstart");
    r.onaudiostart = () => {
      trace("audio");
      armSilence();
    };
    r.onspeechstart = () => {
      trace("speech");
      opts?.onVoice?.();
    };
    r.onend = () => {
      rec.current = null;
      setListening(false);
      micMusic(false);
      if (silenceTimer.current) clearTimeout(silenceTimer.current);
      const text = (finalText.current + interimText.current).trim();
      trace("end", text ? `final: ${text.slice(0, 80)}` : "no text recognized");
      setLive({ phase: "idle", voiced: false, text, final: !!text, confidence: null });
      if (text) cb.current.onFinal(text, { confidence: null, ms: null });
      else cb.current.onNoSpeech?.();
    };
    setError(null);
    rec.current = r;
    try {
      r.start();
    } catch (err) {
      rec.current = null;
      if (silenceTimer.current) clearTimeout(silenceTimer.current);
      trace("start-failed", String(err));
      setError(`Mic failed to start: ${err}`);
      return;
    }
    micMusic(true);
    setListening(true);
    setLive({ phase: "listening", text: "", final: false, level: null, voiced: false, confidence: null });
  }, [trace]);

  const stop = useCallback(() => rec.current?.stop(), []);

  // Hold spacebar (outside text fields) to talk.
  useEffect(() => {
    const typing = (t: EventTarget | null) =>
      t instanceof HTMLElement && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && typing(e.target) && !e.repeat) trace("space-ignored", "focus is in a text field; click the page background first");
      if (e.code !== "Space" || typing(e.target)) return;
      e.preventDefault();
      if (!e.repeat) start();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || typing(e.target)) return;
      e.preventDefault();
      stop();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [start, stop, trace]);

  return { listening, error, supported, start, stop, micLog };
}
