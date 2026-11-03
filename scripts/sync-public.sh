#!/usr/bin/env bash
# Update a clone of the public repo from this checkout: rebuild the public tree (allowlist + privacy check, via
# publish.sh) in a temp folder, then mirror it into the clone, so the change lands as a normal commit on top of
# the public history. It never commits or pushes; review `git -C CLONE diff` and commit yourself.
#   scripts/sync-public.sh CLONE
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLONE="${1:?usage: scripts/sync-public.sh CLONE (a git clone of the public repo)}"
[ -d "$CLONE/.git" ] || { echo "$CLONE isn't a git clone" >&2; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
"$ROOT/scripts/publish.sh" "$TMP/tree" >/dev/null
rsync -a --delete --exclude .git "$TMP/tree/" "$CLONE/"
git -C "$CLONE" add -A
git -C "$CLONE" status --short
