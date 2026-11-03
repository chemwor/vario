#!/usr/bin/env bash
# Build the release archive: dist/vario-<version>.tar.gz (+ .sha256), with the UI prebuilt so installs need no Node.
#   scripts/package.sh            from this checkout (builds web/out if needed)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
v="$(cat VERSION)"
[ -f web/out/index.html ] || (cd web && NEXT_TELEMETRY_DISABLED=1 npx next build >/dev/null)
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
dest="$tmp/vario-$v"
mkdir -p "$dest"
grep -v -E '^\s*(#|$)' scripts/public-files.txt | while read -r p; do
  [ -e "$p" ] || continue
  mkdir -p "$dest/$(dirname "$p")"
  rsync -a --exclude __pycache__ --exclude '*.pyc' --exclude .DS_Store --exclude node_modules --exclude .next \
    --exclude out --exclude '*.tsbuildinfo' "$p" "$dest/$(dirname "$p")/"
done
mkdir -p "$dest/web" && rsync -a web/out "$dest/web/"
mkdir -p dist
COPYFILE_DISABLE=1 tar -czf "dist/vario-$v.tar.gz" -C "$tmp" "vario-$v"
(cd dist && (shasum -a 256 "vario-$v.tar.gz" 2>/dev/null || sha256sum "vario-$v.tar.gz") > "vario-$v.tar.gz.sha256")
echo "dist/vario-$v.tar.gz  $(du -h "dist/vario-$v.tar.gz" | cut -f1)  sha256 $(cut -d' ' -f1 < "dist/vario-$v.tar.gz.sha256")"
