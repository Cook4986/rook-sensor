# Rook Disaster Recovery & Rebuild Guide

If the Raspberry Pi SD card becomes corrupted (e.g., due to an unexpected power loss or thermal shutdown during a write), follow this verified process to re-provision the system to a production state without leaking credentials.

## Step 1: Secure Flash (Raspberry Pi Imager)
You **must** use the Raspberry Pi Imager advanced settings (⚙️) to pre-configure the system. If you skip this, the Pi will fail to boot headlessly.
1. OS: **Raspberry Pi OS Lite (64-bit)** (Trixie or Bookworm both work)
2. Settings ⚙️:
   - Hostname: `rook.local`
   - SSH: ✅ **Enable password authentication** (Critical: Without this, SSH is disabled)
   - User/Pass: `rook` / your secure password
   - Wi-Fi: Type your SSID exactly. Use a 2.4GHz network if 5GHz fails on boot.

## Step 2: Push Secrets (Mac to Pi)
Never commit secrets to the public repository. Before running any setup scripts, inject your API keys via SSH:
```bash
ssh rook@192.168.1.151 "mkdir -p ~/rook-env"
ssh rook@192.168.1.151 "cat > ~/rook-env/.env << 'EOF'
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=matt@mncook.net
SMTP_PASS=your_16_char_app_password
NOTIFY_EMAIL=matt@mncook.net
LATITUDE=40.7128
LONGITUDE=-74.0060
FLIP_180=1
EOF"
```

## Step 3: Run the Rebuild Script
Deploy the `setup_pi.sh` script to the Pi and run it. 
```bash
scp app/setup_pi.sh rook@192.168.1.151:~/
ssh rook@192.168.1.151 "bash ~/setup_pi.sh"
```
**Important Gotchas Accounted For in `setup_pi.sh`:**
1. **Camera Kernel Driver**: On Pi 5, the `arducam-pivariety` overlay is natively integrated into the OS. You **do not** need to compile the kernel driver (`-p kernel_driver`), doing so will fail. The script only installs the user-space `libcamera_dev` and `libcamera_apps` patches.
2. **Dependencies**: `libatlas-base-dev` is deprecated on Trixie; the script installs `libopenblas-dev` instead to satisfy numpy/scipy.
3. **Environment**: Python packages must be installed in a venv using `--system-site-packages` so they can interface with the system-installed `picamera2`.

## Step 4: Verify Subsystems
Reboot the Pi, then run the engine benchmark to verify the camera (`dtoverlay=arducam-pivariety,cam1`), OpenCV motion gating, and YOLO inference.
```bash
ssh rook@192.168.1.151 'source ~/rook-env/bin/activate && python3 ~/frame_test.py --email'
```
Watch your inbox (and spam folder) to confirm that the SMTP image dispatch is functioning. Start the service with `sudo systemctl enable --now rook.service`.
