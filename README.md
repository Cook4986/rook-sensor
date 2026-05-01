<p align="center">
  <img src="assets/rook_logo.png" alt="Rook" width="200">
</p>

<h1 align="center">Rook</h1>
<p align="center"><strong>Privacy-First, Visual-to-Emoji Ambient Monitor</strong></p>
<p align="center">
  Edge AI on a Raspberry Pi 5 — translates street activity into emoji SMS dashboards.<br>
  No video saved. No cloud inference. Just signal.
</p>

---

## What It Does

Rook is a window-mounted camera system that watches a street, sidewalk, or park and texts you simple emoji summaries of what's happening — `📦🚚` for a delivery, `🦌` for wildlife, `🚨` for an emergency. It runs 24/7 on a Raspberry Pi 5 with a Sony STARVIS sensor and YOLOv11n, processing everything locally at the edge.

**Core principles:**

- **Absolute privacy** — no raw video is saved or transmitted (inherently GDPR/CCPA compliant)
- **Signal over noise** — absorbs baseline activity, texts only when specific events occur
- **Always on, low bandwidth** — headless operation, only sends tiny text messages

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Pi 5  (Cortex-A76 @ 2.4 GHz)                  │
│                                                 │
│  Stage 1 — Fast Motion Loop (MOG2)              │
│  ├─ 320×240 frame every 2–3s                    │
│  ├─ Zone mask (ignore tree / window frame)      │
│  └─ No motion → sleep. Zero YOLO cost.          │
│                                                 │
│  Stage 2 — YOLO Inference (on-demand)           │
│  ├─ 640×480 → YOLOv11n (~80ms/frame)            │
│  ├─ State machine → emoji translation           │
│  └─ Twilio SMS (rate-limited, 60s min)           │
│                                                 │
│  Thermal watchdog │ Hardware watchdog (bcm2835)  │
└─────────────┬───────────────────────────────────┘
              │ HTTPS POST
              ▼
┌─────────────────────────────────────────────────┐
│  Cloud (optional dashboard)                     │
│  Supabase (events + config) │ Vercel (Next.js)  │
│  Cloudflare R2 (emergency frames, 24h TTL)      │
└─────────────────────────────────────────────────┘
```

## Repo Structure

```
rook-sensor/
├── app/                    # All software — runs on the Pi 5
│   ├── frame_test.py       # FRAME viewfinder + YOLO benchmark
│   ├── setup_pi.sh         # One-shot Pi setup (OS hardening + drivers + Python)
│   └── deploy_to_pi.sh     # SCP deploy helper (run from Mac)
├── device/                 # Hardware documentation
│   ├── assembly.md         # Step-by-step build guide
│   └── bom.md              # Bill of materials + purchase records
├── assets/
│   └── rook_logo.png
├── .gitignore
└── README.md
```

## Hardware

| Part | Model | Role |
|------|-------|------|
| **Compute** | Raspberry Pi 5 — 2 GB | Quad-core Cortex-A76 @ 2.4 GHz. Headless, 64-bit. |
| **Sensor** | Arducam B0444 (IMX462 STARVIS) | 2MP, f/1.6 effective, fixed IR-cut, M12 mount. Includes 141° wide-angle + both CSI cables. |
| **Optics** | Included 4.3mm M12 (141° HFOV) | Setup lens. Upgrade to 8mm M12 (~40° HFOV) for distance monitoring. |
| **Storage** | SanDisk 32 GB High Endurance | Endurance-rated flash for 24/7 write cycles. |
| **Thermal** | Easycargo aluminum + copper heatsink kit | Passive cooling, sufficient for <0.2% duty cycle. |
| **Mount** | Juxiamal 41mm PVC suction cups (M5) | 4× window-mount, screw-nut style. |
| **Power** | Besgoods 5V/3A QC 3.0 USB-A charger | + Itramax 10ft flat USB-A→USB-C cable. |

**Prototype cost:** ~$213 all-in. See [`device/bom.md`](device/bom.md) for full purchase records.

## Quick Start

### 1. Flash the SD Card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) → **Pi OS Lite (64-bit, Bookworm)** with these settings:

| Setting | Value |
|---------|-------|
| Hostname | `rook.local` |
| SSH | Enabled (password auth) |
| Username | `rook` |
| Wi-Fi | Your SSID + password, country `US` |

### 2. Assemble Hardware

Attach heatsink → connect 22-pin CSI cable to CAM/DISP 0 → insert SD card → stage power cable. See [`device/assembly.md`](device/assembly.md) for the full step-by-step.

### 3. Deploy & Setup

From your Mac:

```bash
# Deploy scripts to the Pi
bash app/deploy_to_pi.sh rook@rook.local

# SSH in and run setup
ssh rook@rook.local
bash ~/setup_pi.sh
sudo reboot
```

### 4. Validate

After reboot:

```bash
ssh rook@rook.local
source ~/rook-env/bin/activate

# Camera check
rpicam-still -o test.jpg --width 1920 --height 1080 -t 2000

# YOLO benchmark + SMS viewfinder
python3 ~/frame_test.py --benchmark --sms
```

**Target:** YOLOv11n inference < 100ms avg @ 640px. SMS arrives on your phone.

## Emoji Vocabulary

| Category | Emoji | Trigger |
|----------|-------|---------|
| **Routines** | `📦🚚` | Delivery truck |
| | `📬🚐` | Mail carrier |
| | `🗑️🚛` | Trash collection |
| **Patterns** | `👨‍👩‍👧‍👦` | Small group |
| | `🏟️` | Large crowd / event |
| | `🌳` | Scene clear |
| **Residential** | `🧒⚽` | Kids at park |
| | `🐕🦺` | Dog walkers |
| | `🚶‍♂️🌇` | Evening foot traffic |
| **Anomalies** | `🦌` / `🦊` | Wildlife |
| | `🐕⚠️` | Loose dog |
| | `🚲💨` | Cyclist on sidewalk |
| **Emergency** | `🚨` | Flashing lights / spatial rule-breaking — bypasses all rate limits, includes a secure frame link |

**Quiet hours:** 11 PM – 6 AM (suppresses routine alerts, keeps `🚨` active). Configurable via SMS: `QUIET 10PM-7AM`.

## Roadmap

- [x] Hardware procurement + assembly
- [x] Pi 5 OS hardening + driver validation
- [x] YOLOv11n benchmark (< 100ms target)
- [x] SMS viewfinder (FRAME test)
- [ ] MOG2 fast motion loop + zone masking
- [ ] Detection → emoji state machine + rate limiter
- [ ] Quiet hours + SMS command interface
- [ ] Thermal monitor (75°C warn / 85°C shutdown)
- [ ] `systemd` service for boot persistence
- [ ] Next.js dashboard (Supabase + Vercel)
- [ ] v2 housing (3D-printed, silicone suction cups)
- [ ] AI HAT+ upgrade path (Hailo-8L, 13 TOPS)

## License

Private project. All rights reserved.
