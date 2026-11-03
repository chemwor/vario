export type SessionState = "working" | "waiting" | "done" | "error";

export interface Permission {
  request_id: string;
  tool: string;
  input: string;
  activity: string;
}

export interface Session {
  id: string;
  cwd: string;
  name: string;
  state: SessionState;
  activity: string | null;
  snippet: string | null;
  muted: boolean;
  narrate: boolean;
  created_at: number;
  updated_at: number;
  turn_started_at: number | null;
  source: "dashboard" | "terminal";
  readonly: boolean;
  live: boolean;
  permissions: Permission[];
  info?: SessionInfo;
}

/** Detail for the session row, read from the transcript (terminal) or the SDK stream (dashboard). */
export interface SessionInfo {
  model: string | null;
  branch: string | null;
  title: string | null;
  mode: string | null;
  context: number | null; // tokens in the last request
  cost: number | null; // last turn, dashboard sessions only
  tools: number; // tool calls this turn
  files: string[]; // edited this turn, most recent last
  agents: number; // subagents running now
  stuck: boolean; // working, but no tool events for STUCK_MINUTES
  context_window: number; // tokens the model can hold (200k, or 1M for [1m] models)
  outcome: { label: "ok" | "failed" | "partial"; line: string | null } | null; // last turn, from triage
  tests: TestSummary | null; // test runs seen in this session's Bash calls
}

export type TestStatus = "passed" | "failed" | "interrupted" | "unknown";

/** One test run a session made (GET /api/sessions/:id/tests). Counts are null when the output wasn't recognised. */
export interface TestRun {
  id: string;
  cmd: string;
  status: TestStatus;
  framework: string | null;
  passed: number | null;
  failed: number | null;
  skipped: number | null;
  started: number;
  ended: number;
  failures: string[];
  output?: string;
  truncated?: boolean;
}

export interface TestSummary {
  runs: number;
  running: { cmd: string; started: number } | null;
  last?: TestRun;
}

/** What a report is about (GET /api/reports/:id/evidence, GET /api/sessions/:id/turn). */
export type EvidenceItem =
  | {
      kind: "diff";
      tool?: string;
      path: string;
      name: string;
      diff: string;
      state: "pending" | "applied" | "not in file now" | "snippet" | "new file" | "written";
      truncated: boolean;
      edits?: number;
    }
  | { kind: "command"; tool: string; command: string; description: string }
  | { kind: "input"; tool: string; text: string }
  | { kind: "sources"; sources: { title: string; url: string }[] }
  | { kind: "lines"; lines: string[] }
  | { kind: "text"; title: string; text: string };

export interface Evidence {
  items: EvidenceItem[];
  note: string | null;
}

export interface TEvent {
  id: string;
  ts: number;
  role: "user" | "assistant" | "tool" | "system";
  kind: "text" | "tool_use" | "tool_result" | "thinking" | "result" | "error" | "permission";
  text: string;
  tool?: string;
  activity?: string;
  is_error?: boolean;
  partial?: boolean;
}

export interface VoiceState {
  global_muted: boolean;
  tts_online: boolean;
}

export type ReportType = "decision_needed" | "research" | "fyi";
export type ReportStatus = "queued" | "delivered" | "answered" | "skipped";

export interface Report {
  id: number;
  source: "session" | "scout" | "app_drop" | "system";
  project: string;
  type: ReportType;
  urgency: number;
  summary: string;
  body: string;
  created_at: number;
  status: ReportStatus;
  decision_text: string | null;
  decision_routed_to: string | null;
  session_id: string | null;
  triaged: number;
  delivered_at: number | null;
  repeat: number; // identical errors collapsed into this one (x3 escalates it to a decision)
  kind: string | null; // dedupe-key prefix: block | final | error | fail | stuck | ctx ...
  outcome: "ok" | "failed" | "partial" | null; // final reports: how the turn went
  outcome_line: string | null;
  last_seen: number | null;
}

export interface RecentSession {
  id: string;
  cwd: string;
  name: string;
  title: string;
  last_modified: number;
  owned: boolean;
  idle: boolean;
}

export interface BriefingState {
  armed: boolean;
  auto: boolean;
  phase: "off" | "idle" | "speaking" | "listening" | "routing";
  listen_until: number | null;
  listen_s: number;
  remaining: number;
  current: { id: number; project: string; type: string; summary: string } | null;
  last_result: Record<string, unknown> | null;
}

export interface ReplySent {
  report_id: number | null;
  utterance: string;
  action: string;
  say: string;
  routed_to: string | null;
  source: string;
}

export interface Scout {
  id: number;
  brief: string;
  project: string;
  linked_report_id: number | null;
  status: "running" | "done" | "failed";
  created_at: number;
  finished_at: number | null;
  report_id: number | null;
  cost_usd: number | null;
  error: string | null;
  kind: string | null; // research playbook: competition | market | features | users | ...
  session_id: string | null; // the session whose product it researched
  library_path: string | null; // the finished report as markdown (~/Vario/research/<product>/)
}

/** A research playbook (backend/app/research/*.md, or your copy in ~/Vario/research/playbooks). */
export interface Playbook {
  kind: string;
  label: string;
  description: string;
  budget_usd: number;
  path: string;
  custom: boolean;
}

export interface Profile {
  profile: string;
  demo?: boolean; // `vario demo`: made-up data, watermarked
  version?: string;
  work: boolean;
  scouts: boolean;
  stt: { mode: "local" | "webspeech"; engine: string; model: string; ready: boolean; error: string | null };
  triage: { path: "sdk" | "rules"; reason: string };
}

export interface AwakeState {
  holding: boolean;
  reasons: string[];
  since: number | null;
  available: boolean;
}

/** One utterance in the Comms pane. */
export interface CommsEntry {
  id: number;
  ts: number;
  source: string; // mic | ptt | harness | rerun | edit | correction
  text: string;
  heard: string | null; // the original transcript, when this corrected it
  confidence: number | null;
  stt_ms: number | null;
  selected_session_id: string | null;
  mode: string | null;
  action: string | null;
  via: string | null;
  target: string | null;
  descr: string | null;
  status: "received" | "held" | "dispatched" | "cancelled" | "ignored" | "failed" | "rejected";
  /** The chip: dispatched | rejected-low-conf | rejected-unintelligible | rejected-unknown | ignored-speaking |
      ignored-muted | ignored-busy | held | cancelled | failed | received */
  reason: string;
  outcome: string | null;
  routed_to: string | null;
  parent_id: number | null;
  corrected_by: number | null;
  flagged: boolean;
}

/** The confirm card: an utterance waiting out its hold before it's dispatched. */
export interface PendingState {
  id: number;
  text: string;
  source: string;
  action: string;
  target: string | null;
  desc: string;
  mode: string;
  spoken: string | null; // what Athena reads back before the countdown
  reading: boolean; // she's reading it back now; the countdown starts after
  explicit: boolean; // low confidence on a tiered verb: only a click or a spoken "confirm" sends it
  confidence: number | null;
  deadline: number;
  hold_ms: number;
}

/** Server -> browser websocket events */
export type WsEvent =
  | { type: "snapshot"; sessions: Session[]; voice: VoiceState; briefing: BriefingState; awake: AwakeState }
  | { type: "awake"; awake: AwakeState }
  | { type: "briefing_state"; briefing: BriefingState }
  | ({ type: "reply_sent" } & ReplySent)
  | { type: "session_state"; session: Session }
  | { type: "session_removed"; session_id: string }
  | { type: "message_delta"; session_id: string; final: true; event: TEvent; replaces?: string | null }
  | { type: "message_delta"; session_id: string; final: false; key: string; delta: string }
  | { type: "tool_activity"; session_id: string; activity: string; tool?: string }
  | { type: "speak"; session_id: string | null; text: string; kind: string; delivered: boolean }
  | { type: "permission_request"; session_id: string; request_id: string; tool: string; input: string; activity: string }
  | { type: "tts_status"; voice: VoiceState }
  | { type: "report"; report: Report }
  | { type: "scout"; scout: Scout }
  | { type: "comms"; entry: CommsEntry }
  | { type: "pending"; pending: PendingState }
  | { type: "pending_done"; id: number; outcome: "dispatch" | "cancel"; why: string }
  | { type: "alert"; report_id: number; session_id: string | null; text: string; delivered: boolean };
