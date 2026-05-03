#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook Archive Synchronization Script
# ─────────────────────────────────────────────────────────────
# Pulls unclassified frames and Beast Cam wildlife crops from 
# the Pi to the local Mac Dropbox folder for long-term storage
# and downstream model training. 

PI_USER="rook"
PI_HOST="192.168.1.151" 
LOCAL_ARCHIVE="/Users/matthewcook/Library/CloudStorage/Dropbox/Rook/archive"

echo "========================================"
echo "[$(date)] Starting Rook archive sync..."
echo "========================================"

# Ensure local directory exists
mkdir -p "$LOCAL_ARCHIVE"

# Synchronize using rsync:
# -a: archive mode (preserves permissions/times)
# -v: verbose
# -z: compress during transfer
# --ignore-existing: skips files we already downloaded (saves bandwidth)
# NOTE: We specifically DO NOT use --delete so that when the Pi 
# runs its 7-day purge, we keep our copies forever on the Mac.
rsync -avz --ignore-existing -e "ssh -o StrictHostKeyChecking=no" "${PI_USER}@${PI_HOST}:~/rook-archive/" "$LOCAL_ARCHIVE/"

# Also sync the Beast Cam crops
rsync -avz --ignore-existing -e "ssh -o StrictHostKeyChecking=no" "${PI_USER}@${PI_HOST}:~/beast_cam/" "$LOCAL_ARCHIVE/beast_cam/"

echo "[$(date)] Sync complete."
