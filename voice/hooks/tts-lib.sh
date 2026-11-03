#!/usr/bin/env bash
# Shared TTS library, sourced by the hook scripts.
#   speak_async "text"     (or: echo text | speak_async)   -> daemon "speak"
#   notify_async "text"                                    -> daemon "notify" (cached wav)
# Everything goes to bubo, the warm Kokoro daemon, over its socket (BUBO_SOCKET in voice.conf). If the daemon is down it is
# started (it holds its own flock, so double starts are harmless); if it still can't be
# reached the text is spoken with `say`/espeak so you always hear something.

HOOK_DIR="${CLAUDE_HOOK_DIR:-${HOME}/.claude/hooks}"
MUTE_FILE="${HOOK_DIR}/.muted"
WATCH_DIR="${HOOK_DIR}/watchers"
VOICE_HOME="${HOME}/.local/share/claude-voice"
KOKORO_PY="${VOICE_HOME}/venv/bin/python"
DAEMON="${HOOK_DIR}/bubo.py"
BUBO_LOG="${HOOK_DIR}/bubo.log"
CLEAN="${HOOK_DIR}/tts-clean.pl"
[ -f "$CLEAN" ] || CLEAN="$(dirname "${BASH_SOURCE[0]}")/tts-clean.pl"

is_muted() { [ -e "$MUTE_FILE" ]; }

# The Vario backend. When it's up it owns alerts and narration policy.
MC_URL="${VARIO_URL:-${PALLAS_URL:-${MISSION_CONTROL_URL:-http://127.0.0.1:8787}}}"
backend_up() { curl -s -m 0.3 -o /dev/null "$MC_URL/api/health"; }
# post_hook EVENT PAYLOAD -> 0 if the backend accepted it
post_hook() {
  [ "$(printf '%s' "$2" | curl -s -m 1 -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
        --data-binary @- "$MC_URL/api/hooks/$1" 2>/dev/null)" = "200" ]
}

# Markdown cleanup + truncation (stdin -> stdout)
clean_text() { perl "$CLEAN"; }

# _sock_send JSON -> prints bubo's reply. BUBO_SOCKET is relative to the hook dir unless absolute
# (relative avoids the 104-char sun_path limit).
bubo_socket() { local s; s="$(_conf BUBO_SOCKET)"; s="${s:-tts.sock}"; printf '%s' "${s/#\~/$HOME}"; }
_sock_send() { local s; s="$(bubo_socket)"; (cd "$HOOK_DIR" 2>/dev/null && printf '%s\n' "$1" | nc -U -w 2 "$s" 2>/dev/null); }

daemon_alive() { [ "$(_sock_send '{"cmd":"ping"}')" = "pong" ]; }

start_daemon() {
  [ -x "$KOKORO_PY" ] && [ -f "$DAEMON" ] || return 1
  [ "$(wc -c < "$BUBO_LOG" 2>/dev/null || echo 0)" -gt 1048576 ] && : > "$BUBO_LOG"
  CLAUDE_HOOK_DIR="$HOOK_DIR" nohup "$KOKORO_PY" "$DAEMON" >>"$BUBO_LOG" 2>&1 </dev/null &
  disown 2>/dev/null || true
}

_fallback_say() {
  if command -v say >/dev/null 2>&1; then say -v Samantha -r 220 -- "$1"
  elif command -v espeak-ng >/dev/null 2>&1; then espeak-ng -s 175 -- "$1"
  elif command -v powershell.exe >/dev/null 2>&1; then
    CLAUDE_TTS_TEXT="$1" powershell.exe -NoProfile -NonInteractive -Command \
      'Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:CLAUDE_TTS_TEXT)'
  fi
}

# Music while speaking (macOS, Spotify / Apple Music). voice.conf:
#   MUSIC=pause | off (default)       MUSIC_RESUME=on (default) | off
# Only players that are already running and playing are paused (never launched), and only those
# get resumed: once the daemon has been quiet for a moment, unless a newer line has started since.
# Vario, when it's up, does this itself (plus duck mode and the mic); this covers the
# hooks-only path.
MUSIC_PAUSED="${HOOK_DIR}/.music-paused"
MUSIC_GEN="${HOOK_DIR}/.music-gen"
_conf() { [ -f "${HOOK_DIR}/voice.conf" ] && sed -n "s/^$1=//p" "${HOOK_DIR}/voice.conf" | tail -n 1; }
# the backend URL: env first, then VARIO_URL in voice.conf (set by the installer when Vario isn't on :8787)
if [ -z "${VARIO_URL:-}${PALLAS_URL:-}${MISSION_CONTROL_URL:-}" ] && [ -n "$(_conf VARIO_URL)" ]; then MC_URL="$(_conf VARIO_URL)"; fi

quiet_music() {
  command -v osascript >/dev/null 2>&1 || return 0
  [ "$(_conf MUSIC)" = "off" ] && return 0
  local app r
  for app in Spotify Music; do
    r="$(osascript -e "if application \"$app\" is running then tell application \"$app\"
          if player state is playing then
            pause
            return \"paused\"
          end if
        end tell" 2>/dev/null)"
    [ "$r" = "paused" ] && echo "$app" >> "$MUSIC_PAUSED"
  done
}

# resume_music_when_idle TOKEN
resume_music_when_idle() {
  [ "$(_conf MUSIC_RESUME)" = "off" ] && { rm -f "$MUSIC_PAUSED"; return 0; }
  sleep 0.5
  local quiet=0 i=0
  while [ $i -lt 600 ]; do          # ~2 min ceiling
    i=$((i + 1))
    [ "$(cat "$MUSIC_GEN" 2>/dev/null)" = "$1" ] || return 0   # a newer line owns the music now
    case "$(_sock_send '{"cmd":"status"}')" in
      *'"busy": true'*) quiet=0 ;;
      *) quiet=$((quiet + 1)) ;;
    esac
    [ $quiet -ge 3 ] && break
    sleep 0.2
  done
  [ "$(cat "$MUSIC_GEN" 2>/dev/null)" = "$1" ] || return 0
  local claim="${MUSIC_PAUSED}.$$"
  mv "$MUSIC_PAUSED" "$claim" 2>/dev/null || return 0         # nothing paused, or someone else resumed
  local app
  for app in $(sort -u "$claim"); do
    osascript -e "if application \"$app\" is running then tell application \"$app\" to play" >/dev/null 2>&1
  done
  rm -f "$claim"
}

# _deliver CMD TEXT (blocking; callers background it)
_deliver() {
  local json; json="$(jq -nc --arg c "$1" --arg t "$2" '{cmd:$c, text:$t}')" || return 0
  is_muted && return 0
  local token; token="$$.$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
  echo "$token" > "$MUSIC_GEN"
  quiet_music & local qpid=$!   # in parallel: the first sentence is still synthesizing
  _send_speech "$json" "$2"
  wait "$qpid"
  resume_music_when_idle "$token"
}

_send_speech() {
  [ "$(_sock_send "$1")" = "ok" ] && return 0
  start_daemon
  local i=0
  while [ $i -lt 60 ]; do   # model load takes a few seconds on a cold start
    sleep 0.25; i=$((i + 1))
    [ "$(_sock_send "$1")" = "ok" ] && return 0
  done
  _fallback_say "$2"
}

_bg() { ( "$@" ) </dev/null >/dev/null 2>&1 & disown 2>/dev/null || true; }

speak_async() {
  local text="${1-}"
  [ $# -eq 0 ] && text="$(cat)"
  [ -z "$text" ] && return 0
  _bg _deliver speak "$text"
}

notify_async() { _bg _deliver notify "$1"; }

ensure_daemon_async() { daemon_alive || _bg start_daemon; }

# --- transcript watcher management -----------------------------------------
watcher_alive() {   # watcher_alive SESSION_ID
  local pid; pid="$(cat "${WATCH_DIR}/$1.pid" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_watcher() {   # start_watcher TRANSCRIPT SESSION_ID
  mkdir -p "$WATCH_DIR"
  local self; self="$(dirname "${BASH_SOURCE[0]}")"
  CLAUDE_HOOK_DIR="$HOOK_DIR" nohup bash "$self/tts-watch.sh" "$1" "$2" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
}

stop_watcher() {    # stop_watcher SESSION_ID
  local pid; pid="$(cat "${WATCH_DIR}/$1.pid" 2>/dev/null)"
  if [ -n "$pid" ]; then pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; fi
  rm -f "${WATCH_DIR}/$1.pid" "${WATCH_DIR}/$1.fifo"
}

# jq filter: one assistant line -> "uuid<TAB>text-as-json" for main-thread text blocks
ASSISTANT_TEXT_JQ='select(.type == "assistant" and (.isSidechain | not) and .message.model != "<synthetic>")
  | [ .message.content | if type == "string" then . else (.[]? | select(.type == "text") | .text) end ] as $t
  | select(($t | join("") | length) > 0)
  | "\(.uuid)\t\($t | join("\n\n") | @json)"'
