#!/usr/bin/env bash
# Install Daily Pilot's launchd agents (news digest, daily plan, Gmail triage)
# and the wake hook. Run it yourself — nothing is installed automatically.
#
#   scripts/install_launchd.sh                    # wake via sleepwatcher (recommended)
#   scripts/install_launchd.sh --wake watch       # wake via a KeepAlive launchd watcher (no brew)
#   scripts/install_launchd.sh --wake none        # no wake actions
#   scripts/install_launchd.sh --generate-only    # just write plists to launchd/generated/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WAKE=sleepwatcher
GENERATE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wake) WAKE="$2"; shift 2 ;;
    --generate-only) GENERATE_ONLY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
echo "→ Syncing the virtualenv (uv sync)…"
uv sync --quiet
EXE="$ROOT/.venv/bin/daily-pilot"
[[ -x "$EXE" ]] || { echo "missing $EXE" >&2; exit 1; }

OUT="$ROOT/launchd/generated"
rm -rf "$OUT"
"$EXE" launchd-plists --out "$OUT" --wake-mode "$WAKE" --exe "$EXE" >/dev/null
echo "→ Generated plists in $OUT:"; ls -1 "$OUT"
[[ $GENERATE_ONLY == 1 ]] && exit 0

if [[ ! -f "$ROOT/.env" ]]; then
  echo "⚠️  No .env found — jobs will run in DRY-RUN mode and only write to data/logs/."
elif ! grep -q '^DAILY_PILOT_LIVE=1' "$ROOT/.env"; then
  echo "⚠️  DAILY_PILOT_LIVE is not 1 in .env — jobs will run in DRY-RUN mode (output in data/logs/)."
fi

AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS"
for plist in "$OUT"/*.plist; do
  label="$(basename "$plist" .plist)"
  dest="$AGENTS/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  cp "$plist" "$dest"
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "✅ loaded $label"
done

if [[ "$WAKE" == "sleepwatcher" ]]; then
  HOOK="$HOME/.wakeup"
  if [[ -e "$HOOK" ]] && ! grep -q "daily-pilot" "$HOOK"; then
    cp "$HOOK" "$HOOK.backup.$(date +%Y%m%d%H%M%S)"
    echo "→ Backed up your existing ~/.wakeup"
    { echo; echo "# daily-pilot"; echo "\"$EXE\" wake >> \"$ROOT/data/logs/wake.log\" 2>&1 &"; } >> "$HOOK"
  elif [[ ! -e "$HOOK" ]]; then
    printf '#!/bin/sh\n# daily-pilot\n"%s" wake >> "%s/data/logs/wake.log" 2>&1 &\n' "$EXE" "$ROOT" > "$HOOK"
  fi
  chmod +x "$HOOK"
  echo "✅ ~/.wakeup runs 'daily-pilot wake'"
  if ! command -v sleepwatcher >/dev/null; then
    echo
    echo "Next: install and start sleepwatcher (it runs ~/.wakeup on every wake):"
    echo "    brew install sleepwatcher"
    echo "    brew services start sleepwatcher"
  else
    echo "Make sure sleepwatcher is running:  brew services start sleepwatcher"
  fi
fi
echo
echo "Logs: $ROOT/data/logs/   ·   Remove everything: scripts/uninstall_launchd.sh"
