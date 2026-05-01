# Assembly & Testing Guide

> **Goal:** Boxed parts → live YOLO inference → first SMS from the window.

---

## Phase 0 — Inventory Check

| ✅ | Item | Source |
|---|---|---|
| ☐ | Raspberry Pi 5 (2 GB) | Amazon #111-6169035 |
| ☐ | Arducam B0444 (IMX462 STARVIS, M12, 141° lens) | Arducam #000005644 |
| ☐ | 22-pin → 22-pin CSI cable (Pi 5 native) | In Arducam box |
| ☐ | SanDisk 32 GB High Endurance microSD | Amazon #111-7967764 |
| ☐ | Acer USB-C SD card reader | Amazon #111-1323662 |
| ☐ | Easycargo heatsink kit | Amazon #111-1323662 |
| ☐ | Juxiamal 41mm PVC suction cups (6-pack, M5) | Amazon #111-7967764 |
| ☐ | Itramax 10ft flat USB-A → USB-C cable (2-pack) | Amazon #111-6169035 |
| ☐ | Besgoods 5V/3A QC 3.0 USB charger (2-pack) | Amazon #111-2113241 |

> **Power note:** The Besgoods charger is QC 3.0 (USB-A). With the USB-A→USB-C cable, the Pi 5 will **not** negotiate QC/PD — it receives plain 5V. At 5V/3A = 15W this is fine for headless sensor workloads. If you see the ⚡ undervoltage icon, switch to a known 5V/3A adapter.

---

## Phase 1 — Flash the SD Card

~10 minutes.

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Insert the SanDisk microSD into the Acer reader → plug into Mac.
3. Choose OS → **Raspberry Pi OS Lite (64-bit, Bookworm)**.
4. Advanced settings (⚙️):
   - Hostname: `rook.local`
   - SSH: ✅ password auth
   - User/Pass: `rook` / your password
   - Wi-Fi: your SSID, country `US`
   - Locale: America/New_York
5. Write → wait for verification → eject.

---

## Phase 2 — Hardware Assembly

~15 minutes.

### Heatsink

1. Clean the SoC (BCM2712) with the included alcohol wipe.
2. Peel thermal tape backing from the largest aluminum heatsink.
3. Press firmly onto the SoC for 10 seconds.
4. Optional: apply small copper heatsinks to PMIC and Wi-Fi chip.

### Camera Cable

> **Use the 22-pin → 22-pin cable** (Pi 5 native). The 15-pin cable is for Pi 4 only.

1. Lift the black latch on the Pi 5's **CAM/DISP 0** connector (closest to Ethernet).
2. Insert the 22-pin cable, **contacts facing down** (toward PCB). Close latch.
3. On the Arducam board: lift latch, insert other end (contacts toward PCB), close latch.

### Power

Route the flat USB-A→USB-C cable from the charger to the Pi 5's USB-C port. **Do not plug in yet.**

### ✅ Checkpoint

Pi 5 with heatsink attached, CSI cable on CAM/DISP 0, SD card inserted, power staged but unplugged.

---

## Phase 3 — First Boot & OS Hardening

~20 minutes. Run the automated script or follow manually:

**Automated:** `bash ~/setup_pi.sh` (handles Phases 3–5).

**Manual:**

```bash
# SSH in
ssh rook@rook.local

# Update OS
sudo apt update && sudo apt full-upgrade -y
sudo reboot

# After reboot: tmpfs mounts for SD longevity
echo 'tmpfs /tmp tmpfs defaults,noatime,nosuid,size=64m 0 0' | sudo tee -a /etc/fstab
echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,size=32m 0 0' | sudo tee -a /etc/fstab
sudo reboot

# Verify
df -h /tmp /var/log   # Both should show "tmpfs"

# Hardware watchdog
sudo apt install watchdog -y
sudo systemctl enable watchdog && sudo systemctl start watchdog
```

---

## Phase 4 — Camera Driver & Validation

~15 minutes.

```bash
# Install Arducam Pivariety driver
wget -qO install_pivariety_pkgs.sh \
  https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
chmod +x install_pivariety_pkgs.sh
./install_pivariety_pkgs.sh -p kernel_driver
sudo reboot
```

After reboot:

```bash
# Verify sensor detection
v4l2-ctl --list-devices   # Should list arducam / imx462

# Capture test image
rpicam-still -o test.jpg --width 1920 --height 1080 -t 2000

# Pull to Mac for review
scp rook@rook.local:~/test.jpg ~/Desktop/rook_first_light.jpg
```

**Go:** `test.jpg` shows a recognizable image.
**No-go:** Re-seat CSI cable (most common fix), verify connector orientation.

---

## Phase 5 — Software Stack

~25 minutes.

```bash
# System deps
sudo apt install -y python3-pip python3-venv libatlas-base-dev libopenjp2-7 libtiff6

# Virtual environment
python3 -m venv ~/rook-env
source ~/rook-env/bin/activate
pip install --upgrade pip
pip install ultralytics opencv-python-headless picamera2 twilio httpx python-dotenv

# Tailscale (remote access)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up   # Follow the auth URL

# Twilio credentials
cat > ~/rook-env/.env << 'EOF'
TWILIO_ACCOUNT_SID=your_sid_here
TWILIO_AUTH_TOKEN=your_token_here
TWILIO_FROM_NUMBER=+1XXXXXXXXXX
NOTIFY_TO_NUMBER=+1XXXXXXXXXX
EOF
```

### Validation

| Test | Expected |
|------|----------|
| `python3 -c "import cv2; print(cv2.__version__)"` | 4.x.x |
| `python3 -c "from ultralytics import YOLO; print('OK')"` | OK |
| `python3 -c "from picamera2 import Picamera2; print('OK')"` | OK |
| `tailscale status` | Node "rook" online |
| YOLOv11n benchmark (`python3 ~/frame_test.py --benchmark`) | < 100ms avg |

---

## Phase 6 — Window Mount & Integration

~30 minutes.

1. Clean the left pane of the 2nd-floor window.
2. Attach **4× suction cups** in a rectangle matching the Pi 5 footprint.
3. Secure Pi + camera to the suction cup bolts (zip ties for v1; proper housing in v2).
4. Angle camera **~15° downward** toward the street/crosswalk.
5. Route flat USB-C cable along the window sill to the outlet.

### Wi-Fi Signal

```bash
iwconfig wlan0 | grep -i "signal level"
# Acceptable: -30 to -65 dBm
```

### SMS Viewfinder Test

```bash
source ~/rook-env/bin/activate
python3 ~/frame_test.py --benchmark --sms
```

### Thermal Baseline

```bash
vcgencmd measure_temp
# Expected: 45–55°C with passive heatsink
```

**Go:** SMS received with YOLO detections from window view. Temp stable. Wi-Fi strong.
