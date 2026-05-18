#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook — Deploy scripts to the Pi
# Run this FROM YOUR MAC after the Pi has booted and you can SSH.
# Usage: bash deploy_to_pi.sh [hostname]
# ─────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${1:-rook@rook.local}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "📤 Deploying Rook scripts to ${HOST}..."

scp "${SCRIPT_DIR}"/*.py "${HOST}:~/"
scp "${SCRIPT_DIR}"/*.sh "${HOST}:~/"
chmod +x "${SCRIPT_DIR}"/*.sh

echo ""
echo "✅ Deployed! SSH in and run:"
echo "   ssh ${HOST}"
echo "   bash ~/setup_pi.sh    # Full Phase 3-5 setup"
echo "   sudo reboot           # Required after driver install"
echo "   # After reboot:"
echo "   source ~/rook-env/bin/activate"
echo "   sudo systemctl restart rook"
echo "   python3 ~/frame_test.py --benchmark --sms"
