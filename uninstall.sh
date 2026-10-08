#!/usr/bin/env bash
# Removes desktop-sense hooks, start-at-login and the `ds` link.
#   ./uninstall.sh            # keeps your recorded data
#   ./uninstall.sh --purge    # also deletes data/, config.json and .venv
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [ -x "$PY" ]; then
  "$PY" "$ROOT/ds.py" stop
  "$PY" "$ROOT/ds.py" autostart off
  "$PY" "$ROOT/ds.py" setup --remove
else
  echo "No .venv found, so the daemon, start-at-login entry and AI-tool hooks could not be removed automatically."
  echo "Remove by hand: ~/Library/LaunchAgents/io.angletech.desktop-sense.plist and the desktop-sense hook entries"
  echo "in ~/.claude/settings.json, ~/.codex/hooks.json, ~/.gemini/settings.json."
fi

if [ -L "$HOME/.local/bin/ds" ] && [ "$(readlink "$HOME/.local/bin/ds")" = "$ROOT/bin/ds" ]; then
  rm -f "$HOME/.local/bin/ds"
fi

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$ROOT/data" "$ROOT/.venv" "$ROOT/config.json"
  echo "Deleted recorded data, config.json and .venv."
else
  echo "Your recorded data is still in $ROOT/data (run with --purge to delete it)."
fi
echo "desktop-sense is uninstalled. You can delete this folder now."
