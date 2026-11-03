"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { SttMeta } from "@/lib/usePushToTalk";
import { usePushToTalk } from "@/lib/usePushToTalk";
import { setTheme } from "./ThemeToggle";
import { Button, Label } from "./ui";

/* First-run setup. Every step can be skipped; a skipped step becomes a warning in the Health panel. */

type StepId = "deps" | "models" | "hooks" | "sound" | "mic" | "prefs";
const STEPS: { id: StepId; title: string; blurb: string }[] = [
  { id: "deps", title: "Dependencies", blurb: "What Vario needs on this Mac. Nothing is installed without you pressing a button." },
  { id: "models", title: "Voice models", blurb: "Kokoro speaks, Whisper listens. Both run on this Mac; every file is checked against a pinned sha256." },
  { id: "hooks", title: "Claude Code hooks", blurb: "Hooks let Claude Code sessions report to Vario. Review the exact change to your settings before it's written." },
  { id: "sound", title: "Test sound", blurb: "A chime through your speakers, then a line from Bubo, the voice daemon." },
  { id: "mic", title: "Microphone", blurb: "Say something. What Whisper hears appears live, with its confidence." },
  { id: "prefs", title: "Theme and voice", blurb: "How Vario looks and sounds. You can change both later." },
];

export interface SetupState {
  first_run: boolean;
  finished: boolean;
  steps: Record<StepId, { status: "pending" | "done" | "skipped"; note: string; at: number | null }>;
  labels: Record<StepId, string>;
  jobs: Record<string, Job>;
  warnings: { step: StepId; label: string; note: string }[];
}
interface Job {
  running?: boolean;
  ok?: boolean | null;
  done?: number;
  total?: number;
  checked?: number;
  files?: Record<string, string>;
  bad?: string[];
  error?: string | null;
  verify_only?: boolean;
  log?: string[];
}
interface Dep {
  id: string;
  label: string;
  ok: boolean;
  required: boolean;
  version: string | null;
  fix: string;
  action?: string;
}
interface ModelSpec {
  key: string;
  label: string;
  repo: string;
  revision: string;
  files: number;
  bytes: number;
  custom: boolean;
}
interface HookPlan {
  ok: boolean;
  error: string | null;
  settings: string;
  settings_exists: boolean;
  diff: string[];
  changes: boolean;
  files: { name: string; dest: string; state: "new" | "update" | "same" }[];
  source: string | null;
  version: string | null;
  installed: { version: string | null; installed_at: number | null };
  hook_dir: string;
  backup?: string | null;
}
interface Voice {
  id: string;
  name: string;
  desc: string;
  current: boolean;
}

const mb = (n: number) => `${Math.round(n / 1e6).toLocaleString()} MB`;

function Status({ ok, required = true, children }: { ok: boolean | null; required?: boolean; children?: React.ReactNode }) {
  const [sym, cls] = ok === null ? ["…", "text-mute"] : ok ? ["✓", "text-ok"] : required ? ["✗", "text-bad"] : ["!", "text-accent"];
  return (
    <span className={`inline-flex w-5 shrink-0 justify-center font-mono text-[13px] ${cls}`} aria-label={ok ? "ok" : required ? "missing" : "optional, missing"}>
      {sym}
      {children}
    </span>
  );
}

function Bar({ done, total, tone = "work" }: { done: number; total: number; tone?: "work" | "ok" | "bad" }) {
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-[1px] bg-line" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <div className={`h-full transition-[width] duration-300 ${tone === "ok" ? "bg-ok" : tone === "bad" ? "bg-bad" : "bg-work"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

// --- steps -------------------------------------------------------------------------------------------
type Ready = (ok: boolean, note: string) => void;

function DepsStep({ jobs, poll, onReady }: { jobs: Record<string, Job>; poll: () => void; onReady: Ready }) {
  const [deps, setDeps] = useState<Dep[] | null>(null);
  const fetchDeps = useCallback(() => {
    api.get<Dep[]>("/api/setup/deps").then(setDeps, () => setDeps([]));
  }, []);
  const load = () => {
    setDeps(null);
    fetchDeps();
  };
  useEffect(fetchDeps, [fetchDeps]);
  useEffect(() => {
    if (!deps) return;
    const missing = deps.filter((d) => d.required && !d.ok).map((d) => d.label);
    onReady(missing.length === 0, missing.length ? `missing: ${missing.join(", ")}` : "");
  }, [deps, onReady]);
  const rt = jobs.runtime;
  useEffect(() => {
    if (rt && rt.running === false && rt.ok) fetchDeps();
  }, [rt?.running, rt?.ok, fetchDeps, rt]);
  return (
    <div>
      <ul className="divide-y divide-line border-y border-line">
        {(deps ?? []).map((d) => (
          <li key={d.id} className="flex items-baseline gap-3 py-2 text-[13px]">
            <Status ok={d.ok} required={d.required} />
            <span className="w-72 shrink-0 text-fg">{d.label}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-mute" title={d.version ?? ""}>
              {d.ok ? d.version : <span className={d.required ? "text-bad" : "text-accent"}>{d.fix}</span>}
            </span>
            {!d.ok && d.action === "install-runtime" && (
              <Button
                disabled={!!rt?.running}
                onClick={() => api.post("/api/setup/runtime").then(poll)}
              >
                {rt?.running ? "Installing…" : "Install"}
              </Button>
            )}
          </li>
        ))}
        {deps === null && <li className="py-3 text-[13px] text-mute">Checking…</li>}
      </ul>
      {rt?.log && rt.log.length > 0 && (
        <pre className="mt-3 max-h-40 overflow-auto rounded-[4px] border border-line bg-sunk p-2 font-mono text-[11px] leading-4 text-dim">
          {rt.log.join("\n")}
        </pre>
      )}
      <button className="mt-3 text-[12px] text-dim underline underline-offset-4 hover:text-fg" onClick={load}>
        Check again
      </button>
    </div>
  );
}

function ModelsStep({ jobs, poll, onReady }: { jobs: Record<string, Job>; poll: () => void; onReady: Ready }) {
  const [models, setModels] = useState<ModelSpec[]>([]);
  useEffect(() => {
    api.get<ModelSpec[]>("/api/setup/models").then(setModels, () => {});
  }, []);
  useEffect(() => {
    if (!models.length) return;
    const notOk = models.filter((m) => !jobs[`model:${m.key}`]?.ok).map((m) => m.label);
    onReady(notOk.length === 0, notOk.length ? `not verified: ${notOk.join(", ")}` : "");
  }, [models, jobs, onReady]);
  const start = (key: string, verify: boolean) => api.post(`/api/setup/models/${key}${verify ? "?verify=true" : ""}`).then(poll);
  return (
    <ul className="space-y-4">
      {models.map((m) => {
        const j = jobs[`model:${m.key}`];
        const finished = j && j.running === false;
        const tone = finished ? (j.ok ? "ok" : "bad") : "work";
        const fileStates = Object.values(j?.files ?? {});
        const verifying = j?.running && (j.verify_only || !fileStates.includes("downloading"));
        return (
          <li key={m.key} className="rounded-[6px] border border-line p-3">
            <div className="flex items-baseline gap-3">
              <Status ok={finished ? !!j.ok : null} required={!finished || !!j.ok || fileStates.includes("bad")} />
              <span className="text-[14px] text-fg">{m.label}</span>
              <span className="font-mono text-[11px] text-mute">
                {m.repo}@{m.revision} · {m.files} files · {mb(m.bytes)}
              </span>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <div className="flex-1">
                <Bar done={j?.done ?? 0} total={j?.total || m.bytes} tone={tone} />
              </div>
              <span className="w-44 shrink-0 text-right font-mono text-[11px] tabular-nums text-dim">
                {j ? `${mb(j.done ?? 0)} / ${mb(j.total || m.bytes)}` : "not checked"}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-3 text-[12px]">
              <span className={`min-w-0 flex-1 truncate ${finished && !j.ok && (j.error || fileStates.includes("bad")) ? "text-bad" : "text-mute"}`}>
                {!j
                  ? "Verify what's already on disk, or download it."
                  : j.running
                    ? verifying
                      ? `checking sha256… ${j.checked ?? 0}/${m.files} files`
                      : `downloading… ${j.checked ?? 0}/${m.files} files verified`
                    : j.ok
                      ? `all ${m.files} files present, sha256 verified`
                      : j.error ??
                        (fileStates.includes("bad")
                          ? `${fileStates.filter((v) => v === "bad").length} file(s) failed the checksum: ${(j.bad ?? []).slice(0, 3).join(", ")}`
                          : fileStates.every((v) => v === "missing")
                            ? "not downloaded yet"
                            : `${fileStates.filter((v) => v === "missing").length} of ${m.files} files not downloaded yet`)}
              </span>
              {m.custom && <span className="text-accent">STT_MODEL is set to another model; this one is the default</span>}
              <Button disabled={!!j?.running} onClick={() => start(m.key, true)}>
                Verify
              </Button>
              <Button variant="primary" disabled={!!j?.running || (finished && !!j.ok)} onClick={() => start(m.key, false)}>
                {finished && !j.ok && fileStates.includes("bad") ? "Download again" : "Download"}
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function HooksStep({ onDone, onReady }: { onDone: (note: string) => void; onReady: Ready }) {
  const [plan, setPlan] = useState<HookPlan | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<HookPlan | null>(null);
  const fetchPlan = useCallback(() => {
    api.get<HookPlan>("/api/setup/hooks").then(setPlan, (e) => setPlan({ ok: false, error: String(e) } as HookPlan));
  }, []);
  const load = () => {
    setConsent(false);
    fetchPlan();
  };
  useEffect(fetchPlan, [fetchPlan]);
  useEffect(() => {
    if (!plan) return;
    const current = result?.ok || (plan.ok && !plan.changes && plan.files.every((f) => f.state === "same"));
    onReady(!!current, current ? `hooks ${plan.version}` : "hooks not installed (or out of date)");
  }, [plan, result, onReady]);
  if (!plan) return <p className="text-[13px] text-mute">Reading your settings…</p>;
  if (!plan.ok && !plan.diff) return <p className="text-[13px] text-bad">{plan.error}</p>;
  const changed = plan.files.filter((f) => f.state !== "same");
  const install = async () => {
    setBusy(true);
    try {
      const r = await api.post<HookPlan>("/api/setup/hooks", { diff: plan.diff });
      setResult(r);
      if (r.ok) onDone(`hooks ${r.version}`);
      else setPlan(r);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      {result?.ok ? (
        <p className="rounded-[6px] border border-ok/50 p-3 text-[13px] text-ok">
          Installed hooks {result.version}.{result.backup ? ` Your previous settings are saved at ${result.backup}.` : " settings.json didn't need changing."}
        </p>
      ) : null}
      <div>
        <Label>Change to {plan.settings}</Label>
        {plan.changes ? (
          <pre className="mt-2 max-h-72 overflow-auto rounded-[4px] border border-line bg-sunk p-2 font-mono text-[11px] leading-[1.45]" aria-label="settings.json diff">
            {plan.diff.map((l, i) => (
              <div
                key={i}
                className={
                  l.startsWith("+") && !l.startsWith("+++") ? "bg-ok/10 text-ok" : l.startsWith("-") && !l.startsWith("---") ? "bg-bad/10 text-bad" : l.startsWith("@@") ? "text-work" : "text-dim"
                }
              >
                {l || " "}
              </div>
            ))}
          </pre>
        ) : (
          <p className="mt-2 text-[13px] text-mute">No change: Vario&rsquo;s hooks are already registered.</p>
        )}
        {plan.changes && plan.settings_exists && <p className="mt-1 text-[12px] text-mute">A timestamped backup (settings.json.bak-…) is written first.</p>}
      </div>
      <div>
        <Label>Files copied</Label>
        <p className="mt-1 text-[12px] text-mute">
          From {plan.source} (version {plan.version}). Installed now: {plan.installed.version ?? "none"}.
        </p>
        {Object.entries(
          plan.files.reduce<Record<string, HookPlan["files"]>>((g, f) => {
            (g[f.dest.slice(0, f.dest.lastIndexOf("/"))] ??= []).push(f);
            return g;
          }, {}),
        ).map(([dir, fs]) => (
          <div key={dir} className="mt-2">
            <div className="truncate font-mono text-[11px] text-mute" title={dir}>
              {dir.replace(/^\/Users\/[^/]+/, "~")}/
            </div>
            <ul className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px]">
              {fs.map((f) => (
                <li key={f.dest} title={f.dest}>
                  <span className="text-dim">{f.name}</span>{" "}
                  <span className={f.state === "new" ? "text-ok" : f.state === "update" ? "text-accent" : "text-faint"}>{f.state}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {plan.error && <p className="text-[13px] text-bad">{plan.error}</p>}
      {!result?.ok && (plan.changes || changed.length > 0) && (
        <div className="flex flex-wrap items-center gap-4 border-t border-line pt-3">
          <label className="flex items-center gap-2 text-[13px] text-fg">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="h-4 w-4 accent-[var(--sun)]" />
            I&rsquo;ve reviewed this change; write it.
          </label>
          <Button variant="primary" disabled={!consent || busy} onClick={install}>
            {busy ? "Installing…" : "Install hooks"}
          </Button>
          <button className="text-[12px] text-dim underline underline-offset-4 hover:text-fg" onClick={load}>
            Re-read settings
          </button>
        </div>
      )}
    </div>
  );
}

function SoundStep({ onDone }: { onDone: (note: string) => void }) {
  const [res, setRes] = useState<{ chime: boolean; bubo: boolean; muted: boolean; spoke: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const play = async (start = false) => {
    setBusy(true);
    try {
      setRes(await api.post(`/api/setup/sound${start ? "?start=true" : ""}`));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <Button variant="primary" disabled={busy} onClick={() => play(false)}>
          {busy ? "Playing…" : "Play test sound"}
        </Button>
        {res && !res.bubo && (
          <Button disabled={busy} onClick={() => play(true)}>
            {busy ? "Starting Bubo (the first start takes about 20 s)…" : "Start Bubo and try again"}
          </Button>
        )}
      </div>
      {res && (
        <ul className="space-y-1 text-[13px]">
          <li className="flex gap-2"><Status ok={res.chime} /> chime through the speakers</li>
          <li className="flex gap-2">
            <Status ok={res.bubo} /> Bubo (voice daemon) {res.bubo ? "is running" : "isn't running: start it above, or skip; notifications still work"}
          </li>
          {res.bubo && (
            <li className="flex gap-2">
              <Status ok={res.spoke} required={!res.muted} /> {res.muted ? "voice is muted (claude-unmute turns it back on)" : res.spoke ? "spoken line sent" : "Bubo didn't take the line"}
            </li>
          )}
        </ul>
      )}
      {res && (
        <div className="flex items-center gap-3 border-t border-line pt-3 text-[13px]">
          <span className="text-fg">Did you hear it?</span>
          <Button onClick={() => onDone(res.spoke ? "voice heard" : "chime heard, no voice")}>Yes</Button>
          <span className="text-mute">If not: check the output device and volume, then play it again.</span>
        </div>
      )}
    </div>
  );
}

function MicStep({ engine, onDone }: { engine: "local" | "webspeech" | null; onDone: (note: string) => void }) {
  const [interim, setInterim] = useState("");
  const [final, setFinal] = useState<{ text: string; conf: number | null } | null>(null);
  const [none, setNone] = useState(false);
  const ptt = usePushToTalk(
    (t) => setInterim(t),
    (t: string, meta: SttMeta) => {
      setInterim("");
      setFinal({ text: t, conf: meta?.confidence ?? null });
    },
    () => setNone(true),
    engine,
  );
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          onClick={() => {
            setFinal(null);
            setNone(false);
            if (ptt.listening) ptt.stop();
            else ptt.start({ autoStopOnFinal: true });
          }}
          disabled={!ptt.supported}
        >
          {ptt.listening ? "Stop" : final ? "Try again" : "Start mic test"}
        </Button>
        <span className="text-[12px] text-mute">
          {engine === "local" ? "Whisper on this Mac: audio never leaves it." : engine === "webspeech" ? "Browser speech service." : ""}
          {ptt.listening && <span className="ml-2 text-work">listening…</span>}
        </span>
      </div>
      <div className="min-h-[3.5rem] rounded-[6px] border border-line bg-sunk px-3 py-2 text-[15px]" aria-live="polite">
        {final ? (
          <>
            <span className="text-fg">{final.text}</span>
            {final.conf !== null && (
              <span className={`ml-3 font-mono text-[11px] ${final.conf >= 0.7 ? "text-ok" : final.conf >= 0.4 ? "text-accent" : "text-bad"}`}>
                {Math.round(final.conf * 100)}% confidence
              </span>
            )}
          </>
        ) : interim ? (
          <span className="text-dim">{interim}</span>
        ) : (
          <span className="text-faint">{none ? "Didn't hear anything. Check the input device in System Settings → Sound." : "Say “what's the status”."}</span>
        )}
      </div>
      {ptt.error && <p className="text-[13px] text-bad">{ptt.error}</p>}
      {final && (
        <div className="flex items-center gap-3 text-[13px]">
          <Button onClick={() => onDone(`heard “${final.text}”${final.conf !== null ? ` at ${Math.round(final.conf * 100)}%` : ""}`)}>That&rsquo;s right</Button>
          <span className="text-mute">Under 40% is ignored and under 70% asks you to confirm, so speak normally, close to the mic.</span>
        </div>
      )}
    </div>
  );
}

function PrefsStep({ profile, setProfile }: { profile: "personal" | "work"; setProfile: (p: "personal" | "work") => void }) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [pick, setPick] = useState<string | null>(null);
  // the wizard only mounts in the browser (opened from an effect), so the current theme is readable here
  const [theme, setThemeState] = useState<"light" | "dark" | null>(() =>
    typeof document === "undefined" ? null : document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  useEffect(() => {
    api.get<{ voices: Voice[] }>("/api/setup/voices").then((r) => {
      setVoices(r.voices);
      setPick(r.voices.find((v) => v.current)?.id ?? null);
    }, () => {});
  }, []);
  const choose = (id: string) => {
    setPick(id);
    api.post("/api/setup/voice", { voice: id, preview: true }).catch(() => {});
  };
  const opt = (on: boolean) =>
    `rounded-[6px] border px-3 py-1.5 text-[13px] ${on ? "border-fg bg-hover text-fg" : "border-line-strong text-dim hover:text-fg"}`;
  return (
    <div className="space-y-5">
      <div>
        <Label>Theme</Label>
        <div className="mt-2 flex gap-2" role="radiogroup" aria-label="Theme">
          {(["light", "dark"] as const).map((t) => (
            <button key={t} role="radio" aria-checked={theme === t} className={opt(theme === t)} onClick={() => (setTheme(t), setThemeState(t))}>
              {t === "light" ? "Daylight hangar" : "Night flight"}
            </button>
          ))}
        </div>
      </div>
      <div>
        <Label>Voice</Label>
        <p className="mt-1 text-[12px] text-mute">Picking one plays a sample (if Bubo is running) and saves it to voice.conf.</p>
        <div className="mt-2 grid max-h-48 grid-cols-2 gap-1.5 overflow-y-auto sm:grid-cols-4" role="radiogroup" aria-label="Voice">
          {voices.map((v) => (
            <button key={v.id} role="radio" aria-checked={pick === v.id} className={`${opt(pick === v.id)} text-left`} onClick={() => choose(v.id)}>
              <span className="block">{v.name}</span>
              <span className="block text-[11px] text-mute">{v.desc}</span>
            </button>
          ))}
        </div>
      </div>
      <div>
        <Label>Profile</Label>
        <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="Profile">
          <button role="radio" aria-checked={profile === "personal"} className={opt(profile === "personal")} onClick={() => setProfile("personal")}>
            Personal
          </button>
          <button role="radio" aria-checked={profile === "work"} className={opt(profile === "work")} onClick={() => setProfile("work")}>
            Work: scouts off, local speech-to-text only
          </button>
        </div>
      </div>
    </div>
  );
}

// --- the wizard ----------------------------------------------------------------------------------------
export function SetupWizard({ engine, onClose, initialStep }: { engine: "local" | "webspeech" | null; onClose: () => void; initialStep?: string }) {
  const [state, setState] = useState<SetupState | null>(null);
  const [i, setI] = useState(() => Math.max(0, STEPS.findIndex((s) => s.id === initialStep)));
  const [profile, setProfile] = useState<"personal" | "work">("personal");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const again = useRef<() => void>(() => {});
  const poll = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    api.get<SetupState>("/api/setup").then(
      (s) => {
        setState(s);
        // keep polling while a download or install is running
        if (Object.values(s.jobs).some((j) => j.running)) timer.current = setTimeout(() => again.current(), 400);
      },
      () => {},
    );
  }, []);
  useEffect(() => {
    again.current = poll;
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);
  const step = STEPS[i];
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [i]);

  const mark = async (status: "done" | "skipped", note = "") => {
    setState({ ...(await api.post<SetupState>(`/api/setup/steps/${step.id}`, { status, note })), jobs: state?.jobs ?? {}, warnings: state?.warnings ?? [] });
    if (i < STEPS.length - 1) setI(i + 1);
    else await finish();
  };
  const finish = async () => {
    await api.post(`/api/setup/finish?profile=${profile}`);
    onClose();
  };
  const [ready, setReadyMap] = useState<Partial<Record<StepId, { ok: boolean; note: string }>>>({});
  const readyFor = useCallback(
    (id: StepId): Ready => (ok, note) => setReadyMap((m) => (m[id]?.ok === ok && m[id]?.note === note ? m : { ...m, [id]: { ok, note } })),
    [],
  );
  const readyD = useMemo(() => readyFor("deps"), [readyFor]);
  const readyM = useMemo(() => readyFor("models"), [readyFor]);
  const readyH = useMemo(() => readyFor("hooks"), [readyFor]);
  /** Continue: done if the step is complete, otherwise it's recorded as skipped with what's missing. */
  const proceed = () => {
    if (step.id === "prefs") return mark("done");
    const r = ready[step.id];
    if (state?.steps[step.id]?.status === "done" && (!r || r.ok)) return mark("done", state.steps[step.id].note);
    if (r?.ok) return mark("done", r.note);
    return mark("skipped", r?.note || "not completed during setup");
  };
  const st = state?.steps[step.id]?.status;
  const jobs = state?.jobs ?? {};

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ground" role="dialog" aria-modal="true" aria-labelledby="setup-title">
      <div className="mx-auto flex min-h-full max-w-4xl flex-col px-4 py-8 sm:px-8">
        <div className="flex items-baseline justify-between">
          <h1 id="setup-title" className="font-serif text-[26px] text-fg">Set up Vario</h1>
          <button className="text-[12px] text-dim underline underline-offset-4 hover:text-fg" onClick={onClose}>
            Finish later
          </button>
        </div>
        {/* stepper */}
        <ol className="mt-5 flex flex-wrap gap-x-1 gap-y-2 border-b border-line pb-3">
          {STEPS.map((s, k) => {
            const status = state?.steps[s.id]?.status ?? "pending";
            return (
              <li key={s.id}>
                <button
                  onClick={() => setI(k)}
                  aria-current={k === i ? "step" : undefined}
                  className={`flex items-center gap-1.5 rounded-[6px] px-2.5 py-1 text-[12px] ${k === i ? "bg-raised text-fg" : "text-dim hover:text-fg"}`}
                >
                  <span className={`font-mono ${status === "done" ? "text-ok" : status === "skipped" ? "text-accent" : "text-faint"}`}>
                    {status === "done" ? "✓" : status === "skipped" ? "–" : k + 1}
                  </span>
                  {s.title}
                </button>
              </li>
            );
          })}
        </ol>

        <section className="mt-6 flex-1">
          <h2 ref={heading} tabIndex={-1} className="font-serif text-[20px] text-fg outline-none">
            {i + 1}. {step.title}
          </h2>
          <p className="mb-5 mt-1 max-w-2xl text-[13px] text-dim">{step.blurb}</p>
          {step.id === "deps" && <DepsStep jobs={jobs} poll={poll} onReady={readyD} />}
          {step.id === "models" && <ModelsStep jobs={jobs} poll={poll} onReady={readyM} />}
          {step.id === "hooks" && <HooksStep onReady={readyH} onDone={(n) => api.post(`/api/setup/steps/hooks`, { status: "done", note: n }).then(poll)} />}
          {step.id === "sound" && <SoundStep onDone={(n) => mark("done", n)} />}
          {step.id === "mic" && <MicStep engine={engine} onDone={(n) => mark("done", n)} />}
          {step.id === "prefs" && <PrefsStep profile={profile} setProfile={setProfile} />}
        </section>

        <footer className="mt-8 flex items-center gap-3 border-t border-line pt-4">
          <Button variant="ghost" disabled={i === 0} onClick={() => setI(i - 1)}>
            Back
          </Button>
          <span className="flex-1 text-[12px] text-mute">
            {st === "skipped" ? "Skipped: it shows as a warning in Health until you come back to it." : st === "done" ? "Done." : ""}
          </span>
          <Button onClick={() => mark("skipped", "skipped during setup")}>Skip</Button>
          <Button variant="primary" onClick={proceed}>
            {i === STEPS.length - 1 ? "Finish" : "Continue"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
