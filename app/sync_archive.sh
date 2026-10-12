#!/usr/bin/env bash
# Rook archive sync. Install with install_mac_sync.sh after every change.
# launchd executes ~/bin/rook_sync.sh because Dropbox execution is restricted.
# Local staging/status survive Dropbox failures. No source frames are deleted.
set -euo pipefail
shopt -s nullglob

PI_USER="${ROOK_SYNC_USER:-rook}"
PI_HOST="${ROOK_SYNC_HOST:-rook.local}"
STAGING_DIR="${ROOK_STAGING_DIR:-$HOME/rook-staging}"
DROPBOX_ARCHIVE="${ROOK_ARCHIVE_DIR:-$HOME/Library/CloudStorage/Dropbox/Rook/archive}"
LOCAL_STATUS="$STAGING_DIR/.sync_status.json"
SSH_OPTS=(-o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o BatchMode=yes)
ERRORS=0
STAGED_UNCLASSIFIED=0
STAGED_CLASSIFIED=0
SELF_MTIME=$(date -r "${BASH_SOURCE[0]}" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)
mkdir -p "$STAGING_DIR"

write_status() {
    local STATE="$1"
    cat > "$LOCAL_STATUS.tmp.$$" <<EOF
{
  "last_run": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "state": "$STATE",
  "errors": $ERRORS,
  "script_installed": "$SELF_MTIME",
  "staged_unclassified_frames": $STAGED_UNCLASSIFIED,
  "staged_classified_frames": $STAGED_CLASSIFIED
}
EOF
    mv -f "$LOCAL_STATUS.tmp.$$" "$LOCAL_STATUS"
}
finish() {
    local RC=$?
    trap - EXIT
    if [ "$RC" -ne 0 ]; then ERRORS=$((ERRORS + 1)); fi
    local STATE=success
    if [ "$ERRORS" -gt 0 ]; then STATE=failed; fi
    if ! write_status "$STATE"; then
        echo "ERROR: cannot write local sync status: $LOCAL_STATUS" >&2
        exit 1
    fi
    if ! cp "$LOCAL_STATUS" "$DROPBOX_ARCHIVE/.sync_status.json.tmp.$$" ||
       ! mv -f "$DROPBOX_ARCHIVE/.sync_status.json.tmp.$$" "$DROPBOX_ARCHIVE/.sync_status.json"; then
        ERRORS=$((ERRORS + 1))
        write_status failed || true
        echo "ERROR: Dropbox status write failed; inspect $LOCAL_STATUS" >&2
    fi
    if [ "$ERRORS" -gt 0 ]; then
        echo "[$(date)] Sync FAILED ($ERRORS errors); retain staging and inspect the log." >&2
        exit 1
    fi
    echo "[$(date)] Sync complete; local status: $LOCAL_STATUS"
}
trap finish EXIT
write_status running

echo "[$(date)] Starting sync; installed $SELF_MTIME"
for KIND in unclassified classified beast_cam; do
    mkdir -p "$STAGING_DIR/$KIND" "$DROPBOX_ARCHIVE/$KIND"
done
if ! ssh "${SSH_OPTS[@]}" "$PI_USER@$PI_HOST" 'echo ok' >/dev/null; then
    echo "ERROR: Pi unreachable; archive freshness is unknown." >&2
    exit 1
fi

sync_dir() {
    local REMOTE_DIR="$1" LOCAL_DIR="$2" PATTERN="${3:-*.jpg}"
    local REMOTE_FILES NEW_FILES="" NEW_COUNT=0 f
    if ! REMOTE_FILES=$(ssh "${SSH_OPTS[@]}" "$PI_USER@$PI_HOST" \
        "find $REMOTE_DIR -maxdepth 1 -name '$PATTERN' -printf '%f\n'"); then
        echo "ERROR: could not list $REMOTE_DIR" >&2
        ERRORS=$((ERRORS + 1)); return
    fi
    for f in $REMOTE_FILES; do
        # Camera-generated names contain no spaces or shell metacharacters.
        case "$f" in *[!a-zA-Z0-9_.-]*|.*)
            echo "ERROR: unexpected remote filename" >&2
            ERRORS=$((ERRORS + 1)); return;;
        esac
        if [ ! -s "$LOCAL_DIR/$f" ]; then
            NEW_FILES="$NEW_FILES$f"$'\n'
            NEW_COUNT=$((NEW_COUNT + 1))
        fi
    done
    if [ "$NEW_COUNT" -eq 0 ]; then return; fi
    # Extract into a fresh batch so a failed transfer cannot poison future skips.
    local BATCH
    BATCH=$(mktemp -d "$STAGING_DIR/.transfer.XXXXXX")
    if ! printf '%s' "$NEW_FILES" | ssh "${SSH_OPTS[@]}" "$PI_USER@$PI_HOST" \
        "cd $REMOTE_DIR && tar -cf - -T -" | tar -xf - -C "$BATCH"; then
        echo "ERROR: transfer failed for $REMOTE_DIR; incomplete batch retained at $BATCH" >&2
        ERRORS=$((ERRORS + 1)); return
    fi
    for f in $NEW_FILES; do
        if [ ! -s "$BATCH/$f" ] || ! mv "$BATCH/$f" "$LOCAL_DIR/$f"; then
            echo "ERROR: incomplete transferred file $f" >&2
            ERRORS=$((ERRORS + 1))
        fi
    done
    rmdir "$BATCH" 2>/dev/null || true
    echo "$REMOTE_DIR: transferred $NEW_COUNT files"
}

sync_dir '~/rook-archive/unclassified' "$STAGING_DIR/unclassified"
sync_dir '~/rook-archive/classified' "$STAGING_DIR/classified"
sync_dir '~/rook-archive/classified' "$STAGING_DIR/classified" '*.json'
if REMOTE_DIRS=$(ssh "${SSH_OPTS[@]}" "$PI_USER@$PI_HOST" \
    "if [ -d ~/beast_cam ]; then find ~/beast_cam -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n'; fi"); then
    for DIRNAME in $REMOTE_DIRS; do
        if [[ ! "$DIRNAME" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]]; then
            echo "ERROR: invalid Beast Cam directory" >&2
            ERRORS=$((ERRORS + 1)); continue
        fi
        mkdir -p "$STAGING_DIR/beast_cam/$DIRNAME" "$DROPBOX_ARCHIVE/beast_cam/$DIRNAME"
        sync_dir "~/beast_cam/$DIRNAME" "$STAGING_DIR/beast_cam/$DIRNAME"
    done
else
    echo "ERROR: could not list Beast Cam dates" >&2
    ERRORS=$((ERRORS + 1))
fi

publish_dir() {
    local SRC="$1" DST="$2" f TMP
    mkdir -p "$DST"
    for f in "$SRC"/*.jpg "$SRC"/*.json; do
        # Existing nonempty files are immutable archive entries, not overwritten.
        # A separate catalog/hash audit checks historical partial/conflicting copies.
        if [ -s "$DST/${f##*/}" ]; then continue; fi
        TMP="$DST/.rook-copy-$$-${f##*/}"
        if ! cp "$f" "$TMP" || ! mv "$TMP" "$DST/${f##*/}"; then
            echo "ERROR: publication failed for $f" >&2
            ERRORS=$((ERRORS + 1))
        fi
    done
}
publish_dir "$STAGING_DIR/unclassified" "$DROPBOX_ARCHIVE/unclassified"
publish_dir "$STAGING_DIR/classified" "$DROPBOX_ARCHIVE/classified"
for DIR in "$STAGING_DIR/beast_cam"/20*/; do
    publish_dir "$DIR" "$DROPBOX_ARCHIVE/beast_cam/$(basename "$DIR")"
done
STAGED_UNCLASSIFIED=$(find "$STAGING_DIR/unclassified" -maxdepth 1 -name '*.jpg' | wc -l | tr -d ' ')
STAGED_CLASSIFIED=$(find "$STAGING_DIR/classified" -maxdepth 1 -name '*.jpg' | wc -l | tr -d ' ')
