#!/usr/bin/env bash
# SessionEnd hook: stop this session's transcript watcher.
trap 'exit 0' ERR
source "$(dirname "$0")/tts-lib.sh" 2>/dev/null || exit 0
sid="$(jq -r '.session_id // empty' 2>/dev/null)"
[ -n "$sid" ] || exit 0
stop_watcher "$sid"
rm -f "${WATCH_DIR}/${sid}.seen"
exit 0
