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
# Homebrew's folders are listed too, for shells that don't have them on PATH (e.g. over SSH).
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  p="$(command -v "$c" 2>/dev/null)" || continue
  # Apple's /usr/bin/python3 is a stub that pops up an "install developer tools" dialog when they're missing
  if [ "$p" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
UV="$(command -v uv 2>/dev/null || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$PY" ] && [ -z "$UV" ]; then
  say "Python 3.10 or newer was not found. Either install it with Homebrew:"
  say "    brew install python"
  say "or, if you don't have an admin password, install uv (no admin needed) and run this script again:"
  say "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# --- 2. Virtual environment + dependencies ------------------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  say "Creating virtual environment (.venv)..."
  if [ -n "$PY" ]; then
    "$PY" -m venv "$ROOT/.venv"
  else
    say "No Python 3.10+ found - using uv to download Python 3.12 (no admin password needed)..."
    "$UV" venv --seed --python 3.12 "$ROOT/.venv"
  fi
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
# `autostart on` (re)loads the LaunchAgent, and launchd starts the daemon right away (with the new code
# on a re-install); without autostart, restart it once. A slow first start must not abort the installer.
if [ "$AUTOSTART" = 1 ]; then
  "$ROOT/.venv/bin/python" "$ROOT/ds.py" autostart on
  "$ROOT/.venv/bin/python" "$ROOT/ds.py" start || true
else
  "$ROOT/.venv/bin/python" "$ROOT/ds.py" restart || true
fi

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
