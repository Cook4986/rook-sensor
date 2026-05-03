<p align="center">
  <img src="assets/rook_logo.png" alt="Rook" width="200">
</p>

<p align="center"><strong>Privacy-First, Visual-to-Emoji Yard Monitor</strong></p>
<p align="center">
  Open-source edge AI on a Raspberry Pi 5 — watches your yard and sends emoji alerts.<br>
  No video saved. No cloud inference. No surveillance. Just signal.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
</p>

---

## What It Does

Rook is a sensor system that watches your yard and sends emoji summaries of what's happening — `🚚📦` for a delivery, `🦅` for a hawk, `🐺⚠️` for a possible coyote, `🌅` at sunrise, `🌆` at sunset.

It runs 24/7 on a Raspberry Pi 5 with a Sony STARVIS camera and [YOLOv11n](https://docs.ultralytics.com/models/yolo11/), processing everything on-device.

| Principle | How |
|-----------|-----|
| **Privacy by design** | No video saved or transmitted. Frames exist only in RAM during inference. |
| **Signal over noise** | Solo cars are counted silently. Only meaningful activity fires a notification. |
| **Always on** | Headless Pi 5 + Slack/Email. No subscriptions, no cloud GPU. |

---

## How It Works

```
Camera (IMX462 STARVIS, 1920×1080)
  │
  ▼
┌──────────────────────────────────────────┐
│  Stage 1 — Motion Gate (MOG2)            │
│  640×360 downscale • ~3ms               │
│  Two-stage: pixel count → blob analysis  │
│  No motion? Sleep. YOLO never runs.      │
└──────────┬───────────────────────────────┘
           │ motion > 1000px, blob > 120px²
           ▼
┌──────────────────────────────────────────┐
│  Stage 2 — YOLO Inference (NCNN)         │
│  imgsz=1088 • ~380ms (3× faster than PT)│
│  conf=0.30 day / 0.25 night             │
│  Score-adaptive cooldown (10–60s)        │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Stage 3 — Enrichment                    │
│  • Open-Meteo weather (15-min cache)     │
│  • iNat local species context            │
│  • Color-sensitive bird ID               │
│  • Composite scene heuristics            │
└──────────┬───────────────────────────────┘
           │
           ▼
      Slack + Email alert
```

---

## Emoji Vocabulary

Rook tracks animate subjects only — people, wildlife, vehicles, and atmospheric events. Inanimate infrastructure is ignored.

| Emoji | Event |
|-------|-------|
| 🏟️ | Large crowd (5+ people) |
| 👥 | Group (2–4 people) |
| 🏃 | Runner (solo person + fast motion) |
| 🚴 | Cyclist (bicycle + person) |
| 🌙🚶 | Night walker (quiet hours) |
| 🌂🚶 | Umbrella in use (rain event) |
| 🚚📦 | Moving day (suitcase + person) |
| 🐾🐾🐾 | Animal cluster (3+ animals) |
| 🐺⚠️ | Possible coyote (solo dog, quiet/dawn hours) |
| 🐕⚠️ | Loose dog (solo dog, daytime) |
| 🦅 | Raptor / solo bird |
| 🔴🐦 | Possible cardinal (color-sensitive) |
| 🔵🐦 | Possible bluebird / blue jay (color-sensitive) |
| ✈️⬇️ | Low-flying aircraft |
| 🪁🌬️ | Kite / wind event |
| 🌅 | Sunrise (exposure mode switch) |
| 🌆 | Sunset (exposure mode switch) |
| 🐻 | Bear — immediate high-priority alert |

---

## Notifications

- **Slack**: Real-time emoji alerts. Threshold: score ≥ 8. Startup cooldown: 5 minutes.
- **Email/MMS**: High-priority events only (score ≥ 20), with annotated image attached.
- **Daily Digest**: 3 AM email with activity summary, top event image, and Beast Cam wildlife crops.
- **Heartbeat**: Slack ping every 6 hours confirming the engine is alive.

Score is based on event rarity in an urban yard context. Bear = 100. Solo car = 1. Cooldown shortens for high-score events (bear: 10s; pedestrian: 52s).

---

## Hardware

| Component | Part |
|-----------|------|
| SBC | Raspberry Pi 5 (2GB+) |
| Camera | Arducam B0444 (IMX462 STARVIS, 1/2.8") |
| OS | Debian Trixie (64-bit) |
| Power | 5V/5A USB-C PD |

See [`device/bom.md`](device/bom.md) for the full parts list.

---

## Setup

### 1. Flash OS

Debian Trixie (64-bit) via Raspberry Pi Imager. Enable SSH and set hostname to `rook`.

### 2. Configure Camera

Add to `/boot/firmware/config.txt`:
```ini
camera_auto_detect=0
dtoverlay=arducam-pivariety
```

### 3. Install

```bash
git clone https://github.com/Cook4986/rook-sensor.git
cd rook-sensor/app
chmod +x setup_pi.sh && ./setup_pi.sh
```

### 4. Configure Environment

Create `~/.env`:
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
NOTIFY_EMAIL=you@example.com
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
LATITUDE=40.71
LONGITUDE=-74.00
FLIP_180=1
```

### 5. Start Service

```bash
sudo systemctl enable --now rook.service
```

---

## NCNN Model

The engine uses a YOLOv11n model exported to NCNN format at `imgsz=1088` for ~3× faster inference vs PyTorch on CPU. To regenerate:

```bash
source ~/rook-env/bin/activate
python3 -c "from ultralytics import YOLO; YOLO('yolo11n.pt').export(format='ncnn', imgsz=1088)"
mv yolo11n_ncnn_model yolo11n_1088_ncnn_model
```

The engine automatically falls back to `yolo11n.pt` if the NCNN directory is not found.

---

## Archive Sync (Mac)

Unclassified motion frames are synced from the Pi to Dropbox every 15 minutes via crontab:

```
*/15 * * * * /path/to/rook-sensor/app/sync_archive.sh >> /tmp/rook_sync.log 2>&1
```

Frames are saved at 640×360 (YOLO training resolution) to minimize SD card I/O.

---

## License

MIT — see [LICENSE](LICENSE).
