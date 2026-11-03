#!/usr/bin/env bash
# Live transcript watcher: speaks each new assistant text block as it lands.
# Usage: tts-watch.sh TRANSCRIPT_PATH SESSION_ID   (started by the SessionStart/Stop hooks)
LIB="$(dirname "$0")/tts-lib.sh"
source "$LIB" || exit 0
lib_mtime() { stat -f %m "$LIB" 2>/dev/null || stat -c %Y "$LIB" 2>/dev/null; }
lib_seen="$(lib_mtime)"
tp="$1"; sid="$2"; IDLE_LIMIT=1800
[ -n "$tp" ] && [ -n "$sid" ] || exit 0
mkdir -p "$WATCH_DIR"
pidf="${WATCH_DIR}/${sid}.pid"; seen="${WATCH_DIR}/${sid}.seen"; fifo="${WATCH_DIR}/${sid}.fifo"
echo $$ > "$pidf"; touch "$seen"

rm -f "$fifo"; mkfifo "$fifo" || exit 0
# jq does the streaming + filtering: bash `read` goes byte-at-a-time on pipes, which is
# far too slow for transcript lines carrying large tool results.
tail -n 0 -F "$tp" 2>/dev/null | jq --unbuffered -Rr "fromjson? | $ASSISTANT_TEXT_JQ" > "$fifo" 2>/dev/null &
reader_pid=$!
cleanup() { pkill -P $$ 2>/dev/null; rm -f "$fifo"; [ "$(cat "$pidf" 2>/dev/null)" = "$$" ] && rm -f "$pidf"; }
trap cleanup EXIT
trap 'exit 0' TERM INT HUP
exec 3<"$fifo"

ensure_daemon_async
started=$(date +%s)

while :; do
  if IFS= read -r -t 60 rec <&3; then
    [ -z "$rec" ] && continue
    uuid="${rec%%	*}"
    grep -qxF "$uuid" "$seen" && continue
    echo "$uuid" >> "$seen"
    is_muted && continue
    backend_up && continue      # Vario is running: narration is its call (off by default)
    # A watcher lives as long as its session: pick up an updated tts-lib.sh (install.sh) without a restart.
    if [ "$(lib_mtime)" != "$lib_seen" ]; then source "$LIB" && lib_seen="$(lib_mtime)"; fi
    text="$(printf '%s' "${rec#*	}" | jq -r . | clean_text)"
    [ -z "$(printf '%s' "$text" | tr -d '[:space:][:punct:]')" ] && continue
    speak_async "$text"
  else
    kill -0 "$reader_pid" 2>/dev/null || exit 0
    now=$(date +%s)
    mtime=$(stat -f %m "$tp" 2>/dev/null || stat -c %Y "$tp" 2>/dev/null || echo "$started")
    [ $((now - mtime)) -ge $IDLE_LIMIT ] && [ $((now - started)) -ge $IDLE_LIMIT ] && exit 0
  fi
done
