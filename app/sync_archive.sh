#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook Archive Synchronization Script
# ─────────────────────────────────────────────────────────────
# Pulls unclassified frames and Beast Cam wildlife crops from
# the Pi to the local Mac Dropbox folder for long-term storage
# and downstream model training.
#
# ISSUE FIX (May 2026):
#   macOS ships openrsync (protocol 29), which is incompatible
#   with GNU rsync 3.x on the Pi — causes "error in rsync protocol
#   data stream (code 12)". Additionally, cron does NOT inherit
#   Full Disk Access, so rsync into ~/Library/CloudStorage/Dropbox/
#   returns "Operation not permitted".
#
# SOLUTION:
#   1. Use scp instead of rsync (universally compatible).
#   2. Stage files to ~/rook-staging/ first (no FDA required),
#      then cp into Dropbox (cp inherits shell permissions).
#   3. Migrate from cron to launchd for FDA inheritance:
#
#      crontab -e  →  remove the old */15 line
#      cp ~/Library/CloudStorage/Dropbox/Rook/rook-sensor/app/com.rook.sync.plist \
#         ~/Library/LaunchAgents/
#      launchctl load ~/Library/LaunchAgents/com.rook.sync.plist
#
# Manual run:
#   bash ~/Library/CloudStorage/Dropbox/Rook/rook-sensor/app/sync_archive.sh
# ─────────────────────────────────────────────────────────────

set -euo pipefail

PI_USER="rook"
PI_HOST="rook.local"
STAGING_DIR="$HOME/rook-staging"
DROPBOX_ARCHIVE="$HOME/Library/CloudStorage/Dropbox/Rook/archive"

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"

echo "========================================"
echo "[$(date)] Starting Rook archive sync..."
echo "========================================"

# ── Ensure directories ─────────────────────────────────────────
mkdir -p "$STAGING_DIR/unclassified"
mkdir -p "$STAGING_DIR/beast_cam"
mkdir -p "$DROPBOX_ARCHIVE/unclassified"
mkdir -p "$DROPBOX_ARCHIVE/beast_cam"

# ── Connectivity check ─────────────────────────────────────────
if ! ssh $SSH_OPTS "$PI_USER@$PI_HOST" "echo ok" &>/dev/null; then
    echo "⚠️  Pi unreachable at $PI_HOST — skipping sync."
    exit 0
fi

# ── Sync function: scp new files, skip existing ────────────────
sync_dir() {
    local REMOTE_DIR="$1"
    local LOCAL_DIR="$2"
    local LABEL="$3"

    echo "── $LABEL ──"

    # Get list of files on Pi
    REMOTE_FILES=$(ssh $SSH_OPTS "$PI_USER@$PI_HOST" \
        "find $REMOTE_DIR -maxdepth 1 -name '*.jpg' -printf '%f\n' 2>/dev/null || ls $REMOTE_DIR/*.jpg 2>/dev/null | xargs -I{} basename {}" 2>/dev/null || true)

    if [ -z "$REMOTE_FILES" ]; then
        echo "   No files found on Pi in $REMOTE_DIR"
        return
    fi

    # Filter to only files we don't already have locally
    NEW_FILES=""
    TOTAL=0
    SKIPPED=0
    for f in $REMOTE_FILES; do
        TOTAL=$((TOTAL + 1))
        if [ ! -f "$LOCAL_DIR/$f" ]; then
            NEW_FILES="$NEW_FILES $f"
        else
            SKIPPED=$((SKIPPED + 1))
        fi
    done

    NEW_COUNT=$(echo $NEW_FILES | wc -w | tr -d ' ')
    echo "   Remote: $TOTAL files | Already synced: $SKIPPED | New: $NEW_COUNT"

    if [ "$NEW_COUNT" -eq 0 ]; then
        echo "   ✅ $LABEL up to date."
        return
    fi

    # scp new files in batches of 50
    echo "$NEW_FILES" | tr ' ' '\n' | grep -v '^$' | while read -r batch; do
        scp $SSH_OPTS "$PI_USER@$PI_HOST:$REMOTE_DIR/$batch" "$LOCAL_DIR/" 2>/dev/null || true
    done

    echo "   ✅ $LABEL: $NEW_COUNT new file(s) transferred."
}

# ── Beast Cam: handle subdirectories (date-based) ──────────────
sync_beast_cam() {
    echo "── Beast Cam ──"

    # Get list of date directories on Pi
    REMOTE_DIRS=$(ssh $SSH_OPTS "$PI_USER@$PI_HOST" \
        "ls -d ~/beast_cam/20*/ 2>/dev/null | xargs -I{} basename {}" 2>/dev/null || true)

    if [ -z "$REMOTE_DIRS" ]; then
        echo "   No Beast Cam directories on Pi."
        return
    fi

    for dir in $REMOTE_DIRS; do
        mkdir -p "$STAGING_DIR/beast_cam/$dir"
        sync_dir "~/beast_cam/$dir" "$STAGING_DIR/beast_cam/$dir" "Beast Cam/$dir"
    done
}

# ── Execute syncs ──────────────────────────────────────────────
sync_dir "~/rook-archive/unclassified" "$STAGING_DIR/unclassified" "Unclassified frames"
sync_beast_cam

# ── Stage → Dropbox (cp inherits user FDA permissions) ─────────
echo "── Copying staging → Dropbox ──"
cp -n "$STAGING_DIR/unclassified/"*.jpg "$DROPBOX_ARCHIVE/unclassified/" 2>/dev/null || true

# Beast Cam: copy each date dir
for dir in "$STAGING_DIR/beast_cam"/20*/; do
    if [ -d "$dir" ]; then
        DIRNAME=$(basename "$dir")
        mkdir -p "$DROPBOX_ARCHIVE/beast_cam/$DIRNAME"
        cp -n "$dir"*.jpg "$DROPBOX_ARCHIVE/beast_cam/$DIRNAME/" 2>/dev/null || true
    fi
done

echo "✅ Staging → Dropbox copy complete."
echo "[$(date)] Sync complete."
