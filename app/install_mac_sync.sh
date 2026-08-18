#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook — install the Mac-side archive sync schedule
# ─────────────────────────────────────────────────────────────
# Run this after ANY change to sync_archive.sh. The scheduled job runs a copy
# at ~/bin/rook_sync.sh, not the repo file, because a launchd job cannot read
# from ~/Library/CloudStorage/Dropbox — /bin/bash gets "Operation not
# permitted" trying to execute or copy from there, even though the same job
# writes into Dropbox fine (verified 2026-08-18).
#
# Editing sync_archive.sh without running this script changes nothing about
# what actually runs every 15 minutes. That drift already cost this project six
# weeks of classified/ frames.
#
# Usage: bash install_mac_sync.sh
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL="com.rook.sync"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
SCRIPT_DST="$HOME/bin/rook_sync.sh"

mkdir -p "$HOME/bin" "$HOME/Library/LaunchAgents"

echo "📤 Installing sync script → $SCRIPT_DST"
cp "$SCRIPT_DIR/sync_archive.sh" "$SCRIPT_DST"
chmod +x "$SCRIPT_DST"

echo "📤 Installing launch agent → $PLIST_DST"
cp "$SCRIPT_DIR/${PLIST_LABEL}.plist" "$PLIST_DST"
plutil -lint "$PLIST_DST"

echo "🔄 Reloading launch agent"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "✅ Installed. Verify with:"
echo "   launchctl list | grep rook          # second column is the last exit code"
echo "   tail -20 /tmp/rook_sync.log         # header prints the installed copy's mtime"
echo "   cat ~/Library/CloudStorage/Dropbox/Rook/archive/.sync_status.json"
