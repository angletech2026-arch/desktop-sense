#!/usr/bin/env bash
# desktop-sense installer for macOS: virtual environment, the `ds` command,
# hooks for Claude Code / Codex CLI / Gemini CLI, and start-at-login.
#   ./install.sh                 # everything
#   ./install.sh --no-autostart --no-hooks
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART=1
HOOKS=1
for arg in "$@"; do
  case "$arg" in
    --no-autostart) AUTOSTART=0 ;;
    --no-hooks) HOOKS=0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
say "desktop-sense installer (macOS)"

if [ "$(uname -s)" != "Darwin" ]; then
  say "This script is for macOS. On Windows use install.ps1."
  exit 1
fi

# Folders macOS protects (Documents / Desktop / Downloads) can't be read by a background process
# started at login, and iCloud Drive would sync your screenshots to the cloud.
case "$ROOT" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    say "Please move desktop-sense out of $ROOT first, e.g.:"
    say "    mv \"$ROOT\" ~/desktop-sense && cd ~/desktop-sense && ./install.sh"
    say "(macOS blocks background apps from reading Documents/Desktop/Downloads, and iCloud would upload your screenshots.)"
    exit 1 ;;
esac

# --- 1. Python 3.10+ ---------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.10 or newer was not found. Install it, then run this script again:"
  say "    brew install python"
  exit 1
fi

# --- 2. Virtual environment + dependencies ------------------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  say "Creating virtual environment (.venv)..."
  "$PY" -m venv "$ROOT/.venv"
fi
say "Installing dependencies..."
"$ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$ROOT/requirements.txt"

# --- 3. `ds` on PATH -----------------------------------------------------------
chmod +x "$ROOT/bin/ds"
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/ds" "$HOME/.local/bin/ds"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) say "Add ~/.local/bin to your PATH to use 'ds' everywhere, e.g.:"
     say "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac

# keep recorded data private to this account
chmod 700 "$ROOT"
mkdir -p "$ROOT/data" && chmod 700 "$ROOT/data"

# --- 4. Hooks for AI coding tools -------------------------------------------
if [ "$HOOKS" = 1 ]; then
  say "Connecting AI coding tools..."
  "$ROOT/.venv/bin/python" "$ROOT/ds.py" setup
fi

# --- 5. Start now + at login ------------------------------------------------
if [ "$AUTOSTART" = 1 ]; then
  "$ROOT/.venv/bin/python" "$ROOT/ds.py" autostart on >/dev/null
fi
"$ROOT/.venv/bin/python" "$ROOT/ds.py" restart || true   # first start can be slow; don't skip the steps below

say ""
say "Done. One more step - macOS needs your permission to see window titles and take screenshots:"
say "  System Settings > Privacy & Security > Screen Recording > enable the entry for Python"
say "  (macOS shows a prompt the first time; if you dismissed it, add it there), then run: ds restart"
say "  'ds status' tells you if the permission is missing. After 'brew upgrade python' you may need to grant it again."
say "The first time a Chrome-family browser is in front, macOS also asks whether Python may control it -"
say "that is how incognito windows are detected. Until you allow it, browser windows are title-only (no screenshots)."
say ""
say "Try:  ds status    ds now"
say "Privacy: everything stays in $ROOT/data. Add your own rules in config.json (see README)."
