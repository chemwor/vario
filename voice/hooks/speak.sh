#!/usr/bin/env bash
# Stop hook. The transcript watcher speaks messages as they land, so normally this only
# makes sure the watcher is alive. If it wasn't running (hooks installed mid-session,
# watcher died), speak the final message here once and start the watcher.
trap 'exit 0' ERR
source "$(dirname "$0")/tts-lib.sh" 2>/dev/null || exit 0
command -v jq >/dev/null 2>&1 || exit 0
payload="$(cat)"
[ "$(printf '%s' "$payload" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0
sid="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"
tp="$(printf '%s' "$payload" | jq -r '.transcript_path // empty' 2>/dev/null)"
tp="${tp/#\~/$HOME}"
[ -n "$sid" ] && [ -f "$tp" ] || exit 0
# Vario reports the finished turn ("Webapp: done, 14 tests passing"); keep the watcher alive.
if post_hook stop "$payload"; then
  watcher_alive "$sid" || start_watcher "$tp" "$sid"
  exit 0
fi
watcher_alive "$sid" && exit 0

rec="$(tail -n 500 "$tp" | jq -r "$ASSISTANT_TEXT_JQ" 2>/dev/null | tail -n 1)"
mkdir -p "$WATCH_DIR"
if [ -n "$rec" ]; then
  echo "${rec%%	*}" >> "${WATCH_DIR}/${sid}.seen"   # watcher must not repeat it
  if ! is_muted; then
    text="$(printf '%s' "${rec#*	}" | jq -r . | clean_text)"
    [ -n "$(printf '%s' "$text" | tr -d '[:space:][:punct:]')" ] && speak_async "$text"
  fi
fi
start_watcher "$tp" "$sid"
exit 0
