#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook — Deploy a fine-tuned NCNN model to the Pi (versioned)
#
# Complements deploy_to_pi.sh (which pushes scripts only).
# Pushes a model produced by train_custom_model.py, swaps it in
# atomically via symlink, restarts the engine, health-checks the
# restart, and AUTO-ROLLS-BACK to the previous model on failure.
#
# Layout on the Pi:
#   ~/rook-models/rook26n_v003/          # versioned NCNN dirs (+ model_card.json)
#   ~/yolo26n_1088_ncnn_model  → symlink into ~/rook-models/...
#   (rook_engine.py already loads that path; os.path.isdir follows symlinks)
#
# Usage:
#   bash deploy_model_to_pi.sh              # deploy latest version
#   bash deploy_model_to_pi.sh 003          # deploy specific version
#   bash deploy_model_to_pi.sh 003 rook@rook.local
# ─────────────────────────────────────────────────────────────
set -euo pipefail

MODELS_DIR="$HOME/Library/CloudStorage/Dropbox/Rook/archive/models"
VERSION="${1:-}"
HOST="${2:-rook@rook.local}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
MODEL_LINK="~/yolo26n_1088_ncnn_model"
HEALTH_TIMEOUT=60   # seconds to wait for a healthy engine restart

# ── Resolve version ────────────────────────────────────────────
if [ -z "$VERSION" ]; then
    VERSION=$(ls -d "$MODELS_DIR"/rook26n_v*/ 2>/dev/null | sed 's|.*_v||; s|/||' | sort -n | tail -1)
    [ -n "$VERSION" ] || { echo "❌ No trained models in $MODELS_DIR — run train_custom_model.py first."; exit 1; }
fi
SRC="$MODELS_DIR/rook26n_v${VERSION}"
[ -d "$SRC/ncnn_model" ] || { echo "❌ $SRC/ncnn_model not found."; exit 1; }

# Refuse to deploy a model that failed its release gate (unless it was forced knowingly)
if command -v python3 >/dev/null && [ -f "$SRC/model_card.json" ]; then
    GATE=$(python3 -c "import json;c=json.load(open('$SRC/model_card.json'));print('ok' if c['gate']['passed'] or c['gate'].get('forced') else 'fail')")
    if [ "$GATE" = "fail" ]; then
        echo "❌ Model v${VERSION} failed its release gate — retrain or use --force-export."
        exit 1
    fi
fi

echo "📤 Deploying model rook26n_v${VERSION} to ${HOST}..."

# ── Push versioned dir + model card ───────────────────────────
ssh $SSH_OPTS "$HOST" "mkdir -p ~/rook-models/rook26n_v${VERSION}"
scp -r "$SRC/ncnn_model" "$HOST:~/rook-models/rook26n_v${VERSION}/"
[ -f "$SRC/model_card.json" ] && scp "$SRC/model_card.json" "$HOST:~/rook-models/rook26n_v${VERSION}/"

# ── Atomic swap: remember previous target, then re-point symlink ──
PREV=$(ssh $SSH_OPTS "$HOST" "readlink $MODEL_LINK 2>/dev/null || true")
ssh $SSH_OPTS "$HOST" "
    set -e
    # First deploy on a Pi with a real directory: preserve it as v000
    if [ -d $MODEL_LINK ] && [ ! -L $MODEL_LINK ]; then
        mv $MODEL_LINK ~/rook-models/rook26n_v000_factory
    fi
    ln -sfn ~/rook-models/rook26n_v${VERSION}/ncnn_model $MODEL_LINK
    sudo systemctl restart rook.service
"

# ── Health check: engine must come up and load the model ──────
echo "🩺 Health check (${HEALTH_TIMEOUT}s)..."
HEALTHY=0
for i in $(seq 1 $((HEALTH_TIMEOUT / 5))); do
    sleep 5
    if ssh $SSH_OPTS "$HOST" "systemctl is-active --quiet rook.service && tail -n 50 ~/rook.log | grep -q 'loaded.'"; then
        HEALTHY=1
        break
    fi
done

if [ "$HEALTHY" -eq 1 ]; then
    echo "✅ rook26n_v${VERSION} live on ${HOST}."
    # System-event notification, consistent with 💚 startup / 🌡️ thermal style
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        curl -s -X POST -H 'Content-Type: application/json' \
            -d "{\"text\":\"🧠🔄 Rook model updated → rook26n_v${VERSION} (custom vocabulary live)\"}" \
            "$SLACK_WEBHOOK_URL" >/dev/null || true
    fi
else
    echo "❌ Engine unhealthy after restart — rolling back."
    if [ -n "$PREV" ]; then
        ssh $SSH_OPTS "$HOST" "ln -sfn $PREV $MODEL_LINK && sudo systemctl restart rook.service"
        echo "↩️  Rolled back to previous model ($PREV)."
    else
        ssh $SSH_OPTS "$HOST" "rm -f $MODEL_LINK && [ -d ~/rook-models/rook26n_v000_factory ] && mv ~/rook-models/rook26n_v000_factory $MODEL_LINK; sudo systemctl restart rook.service"
        echo "↩️  Restored factory model directory."
    fi
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        curl -s -X POST -H 'Content-Type: application/json' \
            -d "{\"text\":\"🧠⚠️ Rook model deploy v${VERSION} FAILED health check — rolled back.\"}" \
            "$SLACK_WEBHOOK_URL" >/dev/null || true
    fi
    exit 1
fi
