<p align="center">
  <img src="assets/rook_logo.png" alt="Rook" width="200">
</p>

<p align="center"><strong>Privacy-First, Visual-to-Emoji Ambient Monitor</strong></p>
<p align="center">
  Open-source edge AI on a Raspberry Pi 5 — translates street activity into emoji SMS alerts.<br>
  No video saved. No cloud inference. No surveillance. Just signal.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
</p>

---

## What It Does

Rook is a window-mounted camera system that watches a street, sidewalk, or park and texts you emoji summaries of what's happening — `📦🚚` for a delivery, `🦌` for wildlife, `🚨` for an emergency.

It runs 24/7 on a Raspberry Pi 5 with a Sony STARVIS sensor and [YOLOv11n](https://docs.ultralytics.com/models/yolo11/), processing everything on-device at the edge.

**Why?**

| Principle | How |
|-----------|-----|
| **Privacy by design** | No video is ever saved or transmitted. Frames exist only in RAM during inference, then are discarded. |
| **Signal over noise** | Absorbs baseline activity. Only texts when something specific happens. |
| **Always on, low cost** | Headless Pi + SMS. No subscriptions, no cloud GPU, no app to install. |

---

## How It Works

```
Camera (IMX462 STARVIS)
  │
  ▼  2–3s polling
┌──────────────────────────────────────┐
│  Stage 1 — Motion Gate  (MOG2)       │
│  320×240 • zone-masked • ~3ms/cycle  │
│                                      │
│  No motion? → sleep. Zero YOLO cost. │
└──────────┬───────────────────────────┘
           │ motion detected
           ▼
┌──────────────────────────────────────┐
│  Stage 2 — YOLO Inference            │
│  640×480 • YOLOv11n • ~350ms (CPU)   │
│                                      │
│  State machine → emoji translation   │
│  Rate limiter (60s min between SMS)  │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Twilio SMS → your phone             │
│  "📦🚚" / "🦌" / "🚨"               │
└──────────────────────────────────────┘
```

**Net effect:** Empty street at 3 AM → YOLO never runs. Delivery at noon → SMS within 3 seconds.

---

## Hardware

| Part | Model | Notes |
|------|-------|-------|
| Compute | Raspberry Pi 5 (2 GB) | Quad-core Cortex-A76 @ 2.4 GHz, headless 64-bit |
| Sensor | [Arducam B0444](https://www.arducam.com/product/arducam-2mp-imx462/) (IMX462 STARVIS) | 2MP, fixed IR-cut, M12 mount. **Pivariety** camera (onboard MCU). |
| Storage | SanDisk 32 GB High Endurance | Endurance-rated for 24/7 writes |
| Thermal | Easycargo heatsink kit | Passive aluminum + copper. ~37°C idle, ~46°C under inference. |
| Mount | Juxiamal 41mm PVC suction cups (M5) | 4× window-mount, screw-nut style |
| Power | Besgoods 5V/3A USB-A charger | + Itramax 10ft flat USB-A→USB-C cable |

**Prototype cost:** ~$213 all-in. Full purchase history in [`device/bom.md`](device/bom.md).

---

## Software Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| OS | Raspberry Pi OS Lite 64-bit | Debian Trixie (or Bookworm). Headless, no desktop. |
| Camera driver | Arducam Pivariety (`arducam-pivariety` overlay) | Patched libcamera — required for B0444 MCU. Native `imx462` overlay will not work. |
| Python env | `~/rook-env` (venv with `--system-site-packages`) | System site-packages needed for `picamera2` / libcamera bindings. |
| AI model | [Ultralytics YOLOv11n](https://docs.ultralytics.com/models/yolo11/) | 2.6M params, 6.5 GFLOPs. Pretrained on COCO (80 classes). `conf=0.45`. |
| Inference | PyTorch 2.x (CPU-only, `aarch64`) | ~350ms/frame @ 640px on Cortex-A76. |
| Vision | OpenCV (`opencv-python-headless`) | MOG2 background subtraction for motion gating. |
| Messaging | Twilio SMS (A2P 10DLC registered) | Sole Proprietor campaign. Rate-limited: 60s min between routine sends. |
| Remote access | [Tailscale](https://tailscale.com) | Zero-config VPN. Also serves as OTA update channel (`git pull` + `systemctl restart rook`). |

### OS Hardening

| Measure | Purpose |
|---------|--------|
| `tmpfs` on `/tmp` (64 MB) and `/var/log` (32 MB) | Minimize SD card write wear for 24/7 operation |
| Hardware watchdog (`bcm2835_wdt`) | Auto-reboot on process or OS hang |
| 2 GB swap file (`/swapfile`) | Prevent OOM freezes during inference |
| SSH key auth (ed25519) | Secure remote access |

---

## Quick Start

> Full step-by-step with photos and troubleshooting in [`device/assembly.md`](device/assembly.md).

### 1. Flash

[Raspberry Pi Imager](https://www.raspberrypi.com/software/) → **Pi OS Lite 64-bit** (Trixie or Bookworm) → set hostname `rook`, enable SSH, configure Wi-Fi.

### 2. Assemble

1. Apply heatsink to SoC with thermal tape.
2. Connect CSI cable to **CAM/DISP 1** (farther from Ethernet). Contacts face the Ethernet port.
3. Insert SD card, stage power cable.

> [!WARNING]
> **Use CAM/DISP 1 only.** The B0444 Pivariety camera produces I2C errors (`-121 EREMOTEIO`) on CAM/DISP 0. This was confirmed through extensive debugging — see [`device/assembly.md`](device/assembly.md) for details.

### 3. Deploy

```bash
# From your Mac — push scripts to the Pi
bash app/deploy_to_pi.sh rook@rook.local

# SSH in and run the setup script
ssh rook@rook.local
bash ~/setup_pi.sh    # Handles OS hardening, Arducam driver, Python venv
sudo reboot
```

### 4. Camera Driver (critical)

The B0444 is a **Pivariety** camera — it has an onboard MCU and requires Arducam's patched libcamera. The standard `imx462` overlay will not work.

```bash
# /boot/firmware/config.txt must contain:
camera_auto_detect=0
dtoverlay=arducam-pivariety,cam1

# Install Arducam's patched libcamera (supports Trixie + Bookworm):
./install_pivariety_pkgs.sh -p libcamera_dev
./install_pivariety_pkgs.sh -p libcamera_apps
```

### 5. Validate

```bash
source ~/rook-env/bin/activate

# Camera test — should show arducam-pivariety [1920x1080]
rpicam-still --list-cameras
rpicam-still -o test.jpg --width 1920 --height 1080 -t 2000

# YOLO benchmark
python3 ~/frame_test.py --benchmark
```

### 6. Messaging (Twilio)

Rook uses Twilio for SMS delivery. US numbers require [A2P 10DLC registration](https://www.twilio.com/docs/messaging/guides/10dlc) (Sole Proprietor campaign) before messages will be delivered by carriers.

```bash
# Create ~/rook-env/.env with your Twilio credentials:
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1XXXXXXXXXX
NOTIFY_TO_NUMBER=+1XXXXXXXXXX
```

### 7. Remote Access (Tailscale)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up    # Opens an auth URL — approve in your browser
tailscale ip -4      # Your device's Tailscale IP
```

Once connected, SSH from anywhere: `ssh rook@100.x.x.x`

---

## Emoji Vocabulary

| Category | Emoji | Trigger |
|----------|-------|---------|
| Routines | `📦🚚` | Delivery truck |
| | `📬🚐` | Mail carrier |
| | `🗑️🚛` | Trash collection |
| Patterns | `👨‍👩‍👧‍👦` | Small group |
| | `🏟️` | Large crowd / event |
| | `🌳` | Scene clear |
| Residential | `🧒⚽` | Kids at park |
| | `🐕🦺` | Dog walkers |
| Anomalies | `🦌` / `🦊` | Wildlife |
| | `🐕⚠️` | Loose dog |
| Emergency | `🚨` | Flashing lights — bypasses rate limits |

**Quiet hours:** 11 PM – 6 AM default (routine alerts suppressed, `🚨` stays active).

---

## Site Context

Rook is designed for a **2nd-floor window mount** overlooking a residential street, crosswalk, and park. Key site considerations:

- **Mounting:** Lower portion of window pane, interior glass surface. Suction cups attach flush.
- **Zone masking:** The right ~30% of the frame contains an ornamental tree — the MOG2 motion gate masks this region to suppress false positives from leaf/branch movement.
- **Night vision:** The B0444's fixed IR-cut filter + f/1.6 equivalent aperture + Sony STARVIS sensor handles streetlit scenes well. For unlit areas, upgrade to the B0423 (motorized IR-cut).
- **Power run:** ~2m flat USB-C cable from window sill to wall outlet.

---

## Thermal Monitoring

A background thread reads `/sys/class/thermal/thermal_zone0/temp` every 60 seconds:

| Threshold | Action |
|-----------|--------|
| < 60°C | 🟢 Normal — no action |
| ≥ 75°C | 🟡 SMS warning: `⚠️ Rook thermal warning: 75°C` |
| ≥ 85°C | 🔴 Graceful shutdown — pauses inference, sends `🌡️ Rook thermal shutdown: 85°C`, idles until < 70°C |

Observed temps: **37°C idle**, **46°C under inference** (passive heatsink, indoor mount).

---

## Repo Structure

```
rook-sensor/
├── app/
│   ├── frame_test.py       # FRAME viewfinder + YOLO benchmark
│   ├── setup_pi.sh         # One-shot Pi setup (OS hardening + drivers)
│   └── deploy_to_pi.sh     # SCP deploy helper (run from Mac)
├── device/
│   ├── assembly.md         # Step-by-step build guide
│   └── bom.md              # Bill of materials
├── assets/
│   └── rook_logo.png
├── PRIVACY.md
├── TERMS.md
├── LICENSE                 # MIT
└── README.md
```

---

## Known Issues & Lessons Learned

| Issue | Resolution |
|-------|------------|
| B0444 not detected on CAM/DISP 0 | Use **CAM/DISP 1** — the B0444 Pivariety MCU only works on `cam1`. |
| `dtoverlay=imx462` fails | B0444 is Pivariety, not native IMX462. Use `dtoverlay=arducam-pivariety,cam1`. |
| YOLO inference ~350ms (not ~80ms) | Expected on 2 GB Pi 5 with CPU-only PyTorch. Adequate for motion-gated architecture. Upgrade path: AI HAT+ (Hailo-8L). |
| OOM crash during ONNX export | 2 GB RAM is too tight for ONNX Runtime session optimization. Stick with PyTorch `.pt` model. |
| Twilio SMS blocked (error 30034) | US carriers require A2P 10DLC registration. Register a Sole Proprietor campaign in Twilio Console. |
| Pi freeze under heavy inference | Ensure 2 GB swap is configured. Avoid multi-model benchmarks in a single process. |

---

## Roadmap

- [x] Hardware assembly + camera validation
- [x] OS hardening (tmpfs, watchdog, SSH keys)
- [x] Arducam Pivariety driver + first light (1920×1080 @ 60fps)
- [x] YOLOv11n benchmark (~350ms CPU-only)
- [x] Tailscale VPN remote access
- [x] Twilio account + A2P 10DLC registration
- [ ] MOG2 fast motion loop + zone masking
- [ ] Detection → emoji state machine + SMS rate limiter
- [ ] Quiet hours + SMS command interface (`FRAME`, `QUIET`, `STOP`)
- [ ] Thermal monitor (75°C warn / 85°C shutdown via SMS)
- [ ] `rook.service` — systemd unit for boot persistence + crash recovery
- [ ] Next.js dashboard (Supabase + Vercel)
- [ ] v2 housing (3D-printed enclosure, silicone suction cups)
- [ ] AI HAT+ upgrade (Hailo-8L, 13 TOPS → sub-100ms inference)

### Upgrade Path

The Pi 5's PCIe slot supports the [Raspberry Pi AI HAT+](https://www.raspberrypi.com/products/ai-hat-plus/) (Hailo-8L, 13 TOPS, ~$26). This snap-on accelerator would enable:
- Sub-10ms YOLOv11n inference (vs. ~350ms CPU)
- Continuous real-time monitoring without the MOG2 motion gate
- Heavier models (YOLOv8s, segmentation) or multi-camera pipelines

No housing, power, or software architecture changes required.

---

## Transparency

Rook is built in the open. The entire hardware build, software stack, and design rationale are documented in this repo. Privacy is a core architectural constraint — no raw video is ever saved or transmitted by design.

- [Privacy Policy](PRIVACY.md) — what Rook does and doesn't collect
- [Terms and Conditions](TERMS.md) — SMS program details, intended use

## License

[MIT](LICENSE) — use it, fork it, build your own.
