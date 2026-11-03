#!/usr/bin/env bash
# SessionStart hook: warm the daemon and start the live transcript watcher.
# Prints nothing (SessionStart stdout would be added to Claude's context).
trap 'exit 0' ERR
source "$(dirname "$0")/tts-lib.sh" 2>/dev/null || exit 0
payload="$(cat)"
tp="$(printf '%s' "$payload" | jq -r '.transcript_path // empty' 2>/dev/null)"
sid="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"
[ -n "$tp" ] && [ -n "$sid" ] || exit 0
ensure_daemon_async
stop_watcher "$sid"
start_watcher "${tp/#\~/$HOME}" "$sid"
exit 0
