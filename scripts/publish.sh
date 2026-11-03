#!/usr/bin/env bash
# Assemble the public repository from the allowlist (scripts/public-files.txt) into a fresh folder with a fresh
# git history (one commit), after checking it for personal data. It never pushes.
#   scripts/publish.sh [DEST]      default DEST: dist/public
# The privacy check fails on: this machine's home path or username, anything shaped like an Anthropic key, and
# every term in .private-terms (one per line; that file is local and never published).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$ROOT/dist/public}"
cd "$ROOT"
[ -e "$DEST/.git" ] && { echo "$DEST already has a git repo; pick an empty folder." >&2; exit 1; }
rm -rf "$DEST"; mkdir -p "$DEST"
grep -v -E '^\s*(#|$)' scripts/public-files.txt | while read -r p; do
  [ -e "$p" ] || { echo "  (skipping missing $p)"; continue; }
  mkdir -p "$DEST/$(dirname "$p")"
  rsync -a --exclude __pycache__ --exclude '*.pyc' --exclude .DS_Store --exclude node_modules --exclude .next \
    --exclude out --exclude '*.tsbuildinfo' "$p" "$DEST/$(dirname "$p")/"
done

# --- privacy check ----------------------------------------------------------------------------------
terms=("$HOME" "$(whoami)")
[ -f "$ROOT/.private-terms" ] && while IFS= read -r t; do [ -n "$t" ] && terms+=("$t"); done < "$ROOT/.private-terms"
fail=0
for t in "${terms[@]}"; do
  if hits="$(grep -rIil --exclude=package-lock.json -F -- "$t" "$DEST" 2>/dev/null)"; [ -n "$hits" ]; then
    echo "✗ personal term found ($t):" >&2; echo "$hits" | sed "s#$DEST/#  #" >&2; fail=1
  fi
done
if grep -rIlE 'sk-ant-[A-Za-z0-9_-]{16,}' "$DEST" >/dev/null 2>&1; then echo "✗ something shaped like an API key" >&2; fail=1; fi
if find "$DEST" \( -name '*.sqlite3*' -o -name 'vario.conf' -o -name '*.log' -o -name '.env*' \) | grep -q .; then
  echo "✗ data, config or logs in the tree" >&2; fail=1
fi
[ "$fail" = 0 ] || { echo "Not published: fix the above (or the allowlist) and run again." >&2; exit 1; }
echo "✓ privacy check passed ($(find "$DEST" -type f | wc -l | tr -d ' ') files)"

# --- fresh history: a single commit -------------------------------------------------------------------
cd "$DEST"
git init -q -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-Vario}" -c user.email="${GIT_AUTHOR_EMAIL:-vario@users.noreply.github.com}" \
  commit -q -m "Vario $(cat VERSION)"
echo "✓ $DEST: one commit, $(git ls-files | wc -l | tr -d ' ') files. Review it, then add a remote and push."
