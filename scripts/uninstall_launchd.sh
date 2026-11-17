#!/usr/bin/env bash
# Remove Daily Pilot's launchd agents and its line in ~/.wakeup.
# Leaves sleepwatcher itself installed (remove with: brew services stop sleepwatcher).
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
for plist in "$AGENTS"/com.dailypilot.*.plist; do
  [[ -e "$plist" ]] || continue
  label="$(basename "$plist" .plist)"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "🗑  removed $label"
done

HOOK="$HOME/.wakeup"
if [[ -f "$HOOK" ]] && grep -q "daily-pilot" "$HOOK"; then
  cp "$HOOK" "$HOOK.backup.$(date +%Y%m%d%H%M%S)"
  # drop the marker comment and the line that calls daily-pilot
  grep -v -e '^# daily-pilot$' -e 'daily-pilot" wake' "$HOOK" > "$HOOK.tmp" || true
  if [[ -z "$(grep -v -e '^#!' -e '^[[:space:]]*$' "$HOOK.tmp")" ]]; then
    rm -f "$HOOK" "$HOOK.tmp"
    echo "🗑  removed ~/.wakeup (it only contained daily-pilot)"
  else
    mv "$HOOK.tmp" "$HOOK"; chmod +x "$HOOK"
    echo "✂️  removed daily-pilot from ~/.wakeup (backup kept)"
  fi
fi
echo "Done."
