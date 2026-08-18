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
#      bash install_mac_sync.sh    # installs the plist AND this script
#
# ⚠️  THIS SCRIPT IS A DEPLOYED ARTIFACT, like the Pi-side code.
#   A launchd job cannot READ from ~/Library/CloudStorage/Dropbox at all —
#   /bin/bash gets "Operation not permitted" trying to execute or copy from
#   there, even though the same job can WRITE into Dropbox (verified
#   2026-08-18). So the scheduled sync runs a copy at ~/bin/rook_sync.sh, and
#   editing this file does NOT change what the schedule runs. Re-run
#   install_mac_sync.sh after every change.
#
#   That is not hypothetical: the installed copy froze at May 2026, so
#   classified/ syncing — added here in August — never ran on a schedule and
#   1,151 hard-positive frames sat on the Pi from 2026-07-04 to 2026-08-18.
#   The run header below prints the installed copy's mtime for exactly this
#   reason; if it looks old in /tmp/rook_sync.log, the schedule is stale.
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

SELF_MTIME=$(date -r "${BASH_SOURCE[0]}" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "unknown")

echo "========================================"
echo "[$(date)] Starting Rook archive sync..."
echo "   running: ${BASH_SOURCE[0]} (installed $SELF_MTIME)"
echo "========================================"

# ── Ensure directories ─────────────────────────────────────────
mkdir -p "$STAGING_DIR/unclassified"
mkdir -p "$STAGING_DIR/classified"
mkdir -p "$STAGING_DIR/beast_cam"
mkdir -p "$DROPBOX_ARCHIVE/unclassified"
mkdir -p "$DROPBOX_ARCHIVE/classified"
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
    local PATTERN="${4:-*.jpg}"   # optional glob — classified/ also carries .json sidecars

    echo "── $LABEL ──"

    # Get list of files on Pi
    REMOTE_FILES=$(ssh $SSH_OPTS "$PI_USER@$PI_HOST" \
        "find $REMOTE_DIR -maxdepth 1 -name '$PATTERN' -printf '%f\n' 2>/dev/null || ls $REMOTE_DIR/$PATTERN 2>/dev/null | xargs -I{} basename {}" 2>/dev/null || true)

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

    # Use tar over SSH for fast batch transfer
    echo "$NEW_FILES" | tr ' ' '\n' | grep -v '^$' | ssh $SSH_OPTS "$PI_USER@$PI_HOST" "cd $REMOTE_DIR && tar -cf - -T -" | tar -xf - -C "$LOCAL_DIR/" 2>/dev/null || true

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
sync_dir "~/rook-archive/classified"   "$STAGING_DIR/classified"   "Classified frames"
sync_dir "~/rook-archive/classified"   "$STAGING_DIR/classified"   "Classified sidecars" "*.json"
sync_beast_cam

# ── Stage → Dropbox (cp inherits user FDA permissions) ─────────
echo "── Copying staging → Dropbox ──"
cp -n "$STAGING_DIR/unclassified/"*.jpg "$DROPBOX_ARCHIVE/unclassified/" 2>/dev/null || true
cp -n "$STAGING_DIR/classified/"*.jpg  "$DROPBOX_ARCHIVE/classified/" 2>/dev/null || true
cp -n "$STAGING_DIR/classified/"*.json "$DROPBOX_ARCHIVE/classified/" 2>/dev/null || true

# Beast Cam: copy each date dir
for dir in "$STAGING_DIR/beast_cam"/20*/; do
    if [ -d "$dir" ]; then
        DIRNAME=$(basename "$dir")
        mkdir -p "$DROPBOX_ARCHIVE/beast_cam/$DIRNAME"
        cp -n "$dir"*.jpg "$DROPBOX_ARCHIVE/beast_cam/$DIRNAME/" 2>/dev/null || true
    fi
done

echo "✅ Staging → Dropbox copy complete."

# ── Status breadcrumb ──────────────────────────────────────────
# The scheduled job can WRITE into Dropbox but cannot READ or stat anything
# there, so counts come from the staging dir — never point find/ls at
# $DROPBOX_ARCHIVE, it fails with "Operation not permitted" under launchd.
# This file is the only way to tell, from the workspace alone, whether the
# schedule is alive and which installed copy of the script ran.
STAGED_UNCLASSIFIED=$(find "$STAGING_DIR/unclassified" -maxdepth 1 -name '*.jpg' 2>/dev/null | wc -l | tr -d ' ')
STAGED_CLASSIFIED=$(find "$STAGING_DIR/classified" -maxdepth 1 -name '*.jpg' 2>/dev/null | wc -l | tr -d ' ')
cat > "$DROPBOX_ARCHIVE/.sync_status.json" <<EOF || true
{
  "last_run": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "script": "${BASH_SOURCE[0]}",
  "script_installed": "$SELF_MTIME",
  "staged_unclassified_frames": $STAGED_UNCLASSIFIED,
  "staged_classified_frames": $STAGED_CLASSIFIED
}
EOF

echo "[$(date)] Sync complete."
