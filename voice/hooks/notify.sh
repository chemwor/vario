#!/usr/bin/env bash
# Notification hook. Normal path: POST to Vario, which works out the actual ask
# ("Billing: OK to run the migration?") and speaks it. Fallback if the backend is down:
# speak "<project>: <notification message>" straight to bubo.
trap 'exit 0' ERR
source "$(dirname "$0")/tts-lib.sh" 2>/dev/null || exit 0
payload="$(cat)"
post_hook notification "$payload" && exit 0
is_muted && exit 0
proj="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null)"; proj="${proj##*/}"
msg="$(printf '%s' "$payload" | jq -r '.message // empty' 2>/dev/null)"; msg="${msg#Claude }"
if [ -n "$proj" ] && [ -n "$msg" ]; then
  speak_async "$proj: $msg"
else
  notify_async "${CLAUDE_NOTIFY_PHRASE:-Claude needs your input}"
fi
exit 0
