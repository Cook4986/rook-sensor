#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Rook — Pi 5 First-Boot Setup Script
# Run after flashing & SSH-ing into the Pi for the first time.
# Usage: bash setup_pi.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

echo "═══════════════════════════════════════════"
echo " Rook — Pi 5 Setup (Phase 3 + 4 + 5)"
echo "═══════════════════════════════════════════"

# ── Phase 3: OS Hardening ──────────────────────────────────
echo ""
echo "▶ [Phase 3] Updating OS..."
sudo apt update && sudo apt full-upgrade -y

echo "▶ [Phase 3] Configuring tmpfs mounts for SD longevity..."
if ! grep -q 'tmpfs /tmp' /etc/fstab; then
    echo 'tmpfs /tmp tmpfs defaults,noatime,nosuid,size=64m 0 0' | sudo tee -a /etc/fstab
fi
if ! grep -q 'tmpfs /var/log' /etc/fstab; then
    echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,size=32m 0 0' | sudo tee -a /etc/fstab
fi

echo "▶ [Phase 3] Installing hardware watchdog..."
sudo apt install -y watchdog
sudo systemctl enable watchdog
sudo systemctl start watchdog

# ── Phase 4: Arducam Driver ───────────────────────────────
echo ""
echo "▶ [Phase 4] Installing Arducam Pivariety libcamera patches..."
# Gotcha: Kernel driver compilation is NO LONGER NEEDED for Pi 5 on Trixie/Bookworm.
# The arducam-pivariety overlay is native. We only patch libcamera.
wget -qO install_pivariety_pkgs.sh \
    https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
chmod +x install_pivariety_pkgs.sh
# Note: sudo is required, and will prompt for the pi user password.
sudo ./install_pivariety_pkgs.sh -p libcamera_dev
sudo ./install_pivariety_pkgs.sh -p libcamera_apps

# ── Phase 5: Python Stack ─────────────────────────────────
echo ""
echo "▶ [Phase 5] Installing system dependencies..."
# Gotcha: libatlas-base-dev is unavailable in Trixie. Use libopenblas-dev.
sudo apt install -y python3-pip python3-venv python3-picamera2 libopenblas-dev libopenjp2-7 libtiff6

echo "▶ [Phase 5] Creating Python virtual environment..."
# Gotcha: Must use --system-site-packages for picamera2 bindings
python3 -m venv --system-site-packages ~/rook-env
source ~/rook-env/bin/activate

echo "▶ [Phase 5] Installing Python packages..."
pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install ultralytics opencv-python-headless twilio httpx python-dotenv suntime
pip install --force-reinstall numpy

# ── Phase 5C: Tailscale ───────────────────────────────────
echo ""
echo "▶ [Phase 5C] Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

echo ""
echo "═══════════════════════════════════════════"
echo " ✅ Setup complete!"
echo ""
echo " NEXT STEPS (manual):"
echo "   1. sudo reboot"
echo "   2. After reboot, verify:"
echo "      df -h /tmp /var/log  (both should be tmpfs)"
echo "      v4l2-ctl --list-devices  (should list arducam/imx462)"
echo "      rpicam-still -o test.jpg --width 1920 --height 1080 -t 2000"
echo "   3. sudo tailscale up  (follow the auth URL)"
echo "   4. Edit ~/rook-env/.env with Twilio credentials"
echo "   5. Run the YOLO benchmark:"
echo '      source ~/rook-env/bin/activate && python3 -c "from ultralytics import YOLO; YOLO(\"yolo26n.pt\").export(format=\"ncnn\", imgsz=1088)"'
echo '      mv yolo26n_ncnn_model ~/yolo26n_1088_ncnn_model'
echo '      python3 ~/frame_test.py'
echo "═══════════════════════════════════════════"
