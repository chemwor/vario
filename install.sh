#!/usr/bin/env bash
# Vario installer.
#
#   curl -fsSL https://raw.githubusercontent.com/chemwor/vario/main/install.sh | bash
#
# Installs the `vario` command and the app into ~/.local/share/vario, then starts it; the first launch opens the
# setup wizard in your browser (voice models download from there, not here). Nothing outside ~/.local is touched
# until you approve it in the wizard.
#
# Environment (all optional):
#   VARIO_VERSION   a release tag like v0.1.0 (default: the latest release)
#   VARIO_SOURCE    install from a local .tar.gz or folder instead of downloading
#   VARIO_HOME      where the app and your data live (default ~/.local/share/vario)
#   VARIO_BIN_DIR   where the `vario` command goes (default ~/.local/bin)
#   VARIO_PORT      port for the dashboard (default 8787)
#   VARIO_NO_START  set to 1 to install without starting
set -euo pipefail

REPO="${VARIO_REPO:-chemwor/vario}"
VERSION="${VARIO_VERSION:-latest}"
VARIO_HOME="${VARIO_HOME:-$HOME/.local/share/vario}"
BIN_DIR="${VARIO_BIN_DIR:-$HOME/.local/bin}"
SOURCE="${VARIO_SOURCE:-}"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; X=$'\033[0m'; else B= G= R= Y= D= X=; fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %-12s %s%s%s\n' "$G" "$X" "$1" "$D" "$2" "$X"; }
miss() { printf '  %s✗%s %-12s %s\n' "$R" "$X" "$1" "$2"; MISSING=$((MISSING + 1)); }
opt()  { printf '  %s!%s %-12s %s\n' "$Y" "$X" "$1" "$2"; }
die()  { printf '%s%s%s\n' "$R" "$*" "$X" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

say "${B}Installing Vario${X}"
say

# --- 1. platform and dependencies: check everything first, install nothing if something's missing ------
OS="$(uname -s)"
case "$OS" in
  Darwin) PKG="brew install" ;;
  Linux)  PKG="sudo apt install" ;;
  *) die "Vario v0.1 supports macOS and Linux. Windows isn't supported yet (WSL may work, untested)." ;;
esac
say "Checking what Vario needs ($OS $(uname -m))"
MISSING=0
for t in curl tar; do have "$t" && ok "$t" "found" || miss "$t" "$PKG $t"; done
if have uv; then ok "uv" "$(uv --version)"; else miss "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh   (or: brew install uv)"; fi
if have jq; then ok "jq" "$(jq --version)"; else miss "jq" "$PKG jq   (the Claude Code hooks use it)"; fi
if have claude; then ok "claude" "$(claude --version 2>/dev/null | head -n1)"
else miss "claude" "Claude Code isn't installed: https://docs.claude.com/claude-code"; fi
have tmux && ok "tmux" "$(tmux -V)" || opt "tmux" "optional: lets Vario reply to terminal sessions ($PKG tmux)"
if [ "$OS" = Linux ]; then
  have espeak-ng && ok "espeak-ng" "found" || opt "espeak-ng" "optional: spoken alerts on Linux ($PKG espeak-ng)"
  have notify-send && ok "notify-send" "found" || opt "notify-send" "optional: desktop notifications ($PKG libnotify-bin)"
fi
if [ "$MISSING" -gt 0 ]; then
  say
  die "$MISSING required tool(s) missing; install them and run this again. Nothing was installed."
fi
say

# --- 2. get the release ----------------------------------------------------------------------------
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -n "$SOURCE" ]; then
  [ -e "$SOURCE" ] || die "VARIO_SOURCE doesn't exist: $SOURCE"
  if [ -d "$SOURCE" ]; then
    say "Using the folder $SOURCE"
    mkdir -p "$TMP/src" && (cd "$SOURCE" && tar --exclude node_modules --exclude .next --exclude .venv --exclude data -cf - .) | (cd "$TMP/src" && tar -xf -)
  else
    say "Using the archive $SOURCE"
    mkdir -p "$TMP/src" && tar -xzf "$SOURCE" -C "$TMP/src" --strip-components 1
  fi
else
  if [ "$VERSION" = latest ]; then
    VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
    [ -n "$VERSION" ] || die "Couldn't find the latest release of $REPO."
  fi
  v="${VERSION#v}"
  base="https://github.com/$REPO/releases/download/$VERSION"
  say "Downloading Vario $v"
  curl -fsSL "$base/vario-$v.tar.gz" -o "$TMP/vario.tar.gz" || die "Download failed: $base/vario-$v.tar.gz"
  curl -fsSL "$base/vario-$v.tar.gz.sha256" -o "$TMP/vario.sha256" || die "Checksum download failed."
  want="$(cut -d' ' -f1 < "$TMP/vario.sha256")"
  got="$( (shasum -a 256 "$TMP/vario.tar.gz" 2>/dev/null || sha256sum "$TMP/vario.tar.gz") | cut -d' ' -f1)"
  [ "$want" = "$got" ] || die "Checksum mismatch (expected $want, got $got). Nothing was installed."
  ok "sha256" "$got"
  mkdir -p "$TMP/src" && tar -xzf "$TMP/vario.tar.gz" -C "$TMP/src" --strip-components 1
fi
v="$(cat "$TMP/src/VERSION" 2>/dev/null || echo dev)"

# --- 3. install ----------------------------------------------------------------------------------------
APP="$VARIO_HOME/app/$v"
mkdir -p "$VARIO_HOME/app" "$BIN_DIR"
rm -rf "$APP" && mv "$TMP/src" "$APP"
if [ ! -f "$APP/web/out/index.html" ]; then           # a source checkout: build the UI (needs Node 20.9+)
  have npm || die "This copy has no prebuilt UI and Node isn't installed ($PKG node). Use a release instead."
  say "Building the UI (once)"
  (cd "$APP/web" && NEXT_TELEMETRY_DISABLED=1 npm ci --silent --no-audit --no-fund && NEXT_TELEMETRY_DISABLED=1 npx next build >/dev/null)
  rm -rf "$APP/web/node_modules" "$APP/web/.next"
fi
ln -sfn "$APP" "$VARIO_HOME/current"
ln -sfn "$VARIO_HOME/current/bin/vario" "$BIN_DIR/vario"
ok "vario $v" "$APP"
"$BIN_DIR/vario" version >/dev/null                    # creates the Python environment now, not on first start
ok "python env" "$VARIO_HOME/venv/$v"
say

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    case "${SHELL:-}" in */zsh) rc="~/.zshrc" ;; */bash) rc="~/.bashrc" ;; */fish) rc="~/.config/fish/config.fish" ;; *) rc="your shell's startup file" ;; esac
    say "${Y}$BIN_DIR isn't on your PATH yet.${X} Until it is, run Vario as $BIN_DIR/vario. To fix it for new terminals:"
    if [ "${SHELL:-}" != "${SHELL%/fish}" ]; then say "  fish_add_path $BIN_DIR"
    else say "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> $rc"; fi
    say ;;
esac

say "${B}Done.${X} Start it any time with ${B}vario${X}; ${B}vario doctor${X} checks everything; ${B}vario uninstall${X} removes it."
if [ "${VARIO_NO_START:-0}" != 1 ]; then
  say "Starting Vario. The setup wizard opens in your browser."
  "$BIN_DIR/vario" start
fi
