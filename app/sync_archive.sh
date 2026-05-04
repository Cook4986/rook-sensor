#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook Archive Synchronization Script
# ─────────────────────────────────────────────────────────────
# Pulls unclassified frames and Beast Cam wildlife crops from
# the Pi to the local Mac Dropbox folder for long-term storage
# and downstream model training.
#
# Schedule via Mac crontab (crontab -e):
#   */15 * * * * /Users/matthewcook/Library/CloudStorage/Dropbox/Rook/rook-sensor/app/sync_archive.sh >> /tmp/rook_sync.log 2>&1
#
# Uses rook.local (mDNS) — resilient to DHCP IP changes.
# ─────────────────────────────────────────────────────────────

PI_USER="rook"
PI_HOST="rook.local"
LOCAL_ARCHIVE="/Users/matthewcook/Library/CloudStorage/Dropbox/Rook/archive"

echo "========================================"
echo "[$(date)] Starting Rook archive sync..."
echo "========================================"

# Ensure local directories exist
mkdir -p "$LOCAL_ARCHIVE/unclassified"
mkdir -p "$LOCAL_ARCHIVE/beast_cam"

# rsync flags:
#   -a:               archive mode (recursive, preserve symlinks/owner)
#   -z:               compress in transit
#   --ignore-existing: skip files already downloaded (saves bandwidth)
#   --no-perms:       don't try to set permissions on destination
#   --no-times:       don't try to set mtime — Dropbox VFS blocks this and returns EPERM
#   --size-only:      compare by size (since we skip mtime)
#   -e ssh:           use SSH with known-hosts bypass (hostname may vary)
#   --stats:          print transfer summary
# NOTE: no --delete — Pi's 7-day purge should not remove Mac copies.

RSYNC_OPTS="-az --ignore-existing --no-perms --no-times --size-only --stats \
  -e 'ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10'"

echo "Transfer starting: unclassified frames"
eval rsync $RSYNC_OPTS \
  "${PI_USER}@${PI_HOST}:~/rook-archive/unclassified/" \
  "$LOCAL_ARCHIVE/unclassified/" \
  && echo "✅ Unclassified sync OK" || echo "⚠️  Unclassified sync failed"

echo "Transfer starting: Beast Cam crops"
eval rsync $RSYNC_OPTS \
  "${PI_USER}@${PI_HOST}:~/beast_cam/" \
  "$LOCAL_ARCHIVE/beast_cam/" \
  && echo "✅ Beast Cam sync OK" || echo "⚠️  Beast Cam sync failed"

echo "[$(date)] Sync complete."
