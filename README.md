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

Rook is a sensor system that watches your yard and sends emoji summaries of what's happening — `🚚📦` for a delivery, `🦅` for a hawk, `🐺⚠️` for a possible coyote, `🌅` at sunrise, `🚗🔒` for a car parked over an hour.

It runs 24/7 on a Raspberry Pi 5 with a Sony STARVIS camera and [YOLOv11n](https://docs.ultralytics.com/models/yolo11/), processing everything on-device.

| Principle | How |
|-----------|-----|
| **Privacy by design** | No video saved or transmitted. Frames exist only in RAM during inference. |
| **Signal over noise** | Solo cars counted silently. Static fixtures auto-suppressed. Only meaningful activity fires. |
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
│  Forced scan every 5 min (lingerer check)│
└──────────┬───────────────────────────────┘
           │ motion > threshold OR forced scan
           ▼
┌──────────────────────────────────────────┐
│  Stage 2 — YOLO Inference (NCNN)         │
│  imgsz=1088 • ~150ms (3× faster than PT)│
│  conf=0.45 day / 0.30 night             │
│  Ignored: train, traffic light, boat     │
│  Airborne gate: conf ≥ 0.55             │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Stage 3 — Scene Intelligence            │
│  • SceneFixtureFilter: auto-suppresses   │
│    objects in ≥80% of recent inferences  │
│  • LingererTracker: delayed alerts for   │
│    parked cars (60min) / people (5min)   │
│  • Open-Meteo weather (15-min cache)     │
│  • iNat local species context            │
│  • Color-sensitive bird ID               │
│  • Composite scene heuristics            │
└──────────┬───────────────────────────────┘
           │
           ▼
      Slack + Email alert
      + 24h Emoji Activity Log
```

---

## Emoji Vocabulary

Rook tracks animate subjects — people, wildlife, vehicles, and atmospheric events.
Permanently static scene fixtures are auto-learned and suppressed.

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
| 🐺⚠️ | Possible coyote (spatially isolated dog, quiet/dawn hours) |
| 🐕⚠️ | Loose dog (spatially isolated dog, daytime) |
| 🐕 | Accompanied dog (near a person — assumed on-leash, no alert) |
| 🦅 | Raptor / solo bird |
| 🔴🐦 | Possible cardinal (color-sensitive) |
| 🔵🐦 | Possible bluebird / blue jay (color-sensitive) |
| ✈️⬇️ | Low-flying aircraft |
| 🪁🌬️ | Kite / wind event |
| 🌅 | Sunrise (exposure mode switch) |
| 🌆 | Sunset (exposure mode switch) |
| 🐻 | Bear — immediate high-priority alert |
| 🚗🔒 | Parked car (lingering > 60 min) |
| 🚶⏱️ | Loitering person (lingering > 5 min) |

---

## Notifications

- **Slack**: Real-time emoji alerts. Threshold: score ≥ 15. Quiet hours (11 PM–6 AM) prefix alerts with 🌙 — no email.
- **Email/MMS**: High-priority events only (score ≥ 30), with annotated image attached. Suppressed during quiet hours.
- **Lingering alerts**: Slack-only. Fires when a tracked object holds its scene zone beyond its threshold (car: 60 min, person: 5 min). Re-alerts every 15 min if still present.
- **Daily Digest**: 3 AM email covering the full **previous calendar day**. Includes yesterday's activity counts, cumulative stats (this week / this month / all-time from `~/rook-stats.json`), top event image, and Beast Cam wildlife crops.
- **Heartbeat**: Slack ping every 6 hours confirming the engine is alive.

Score is based on event rarity in an urban yard context. Bear = 100. Solo car = 1 (silent). Congregation bonus: +15 for 3+ objects in scene, +25 for 5+. Cooldown shortens for high-score events.

---

## Scene Intelligence

### Fixture Suppression
`SceneFixtureFilter` tracks which (class, zone) combinations appear in ≥ 80% of the last 60 inferences. Objects that persistently appear in the same screen position are promoted to **fixtures** and silently dropped — preventing a fixed houselight, signpost, or long-parked object from consuming the alert budget. Fixture list resets daily.

### Lingering Object Detection
`LingererTracker` observes objects across consecutive YOLO scans. A periodic forced YOLO run every 5 minutes bypasses the MOG2 motion gate so that objects absorbed into the background model (e.g., a car that stopped moving) are still tracked.

### Suppressed Classes
These COCO classes are never scored or alerted in this deployment:

| Class | Reason |
|-------|--------|
| `train` | No rail infrastructure nearby — misclassifies dark boxy vehicles at distance |
| `traffic light` | Park houselight across the street — persistent false positive |
| `boat` | No navigable water nearby — park fence / reflective surface (observed live) |

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

Create `~/rook-env/.env`:
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
# TEST_EMAIL=1   # Uncomment to email a live frame on next restart (one-shot diagnostic)
```

### 5. Start Service

```bash
sudo systemctl enable --now rook.service
```

---

## Diagnostic Tools

**Live frame test** (standalone — engine must be stopped):
```bash
source ~/rook-env/bin/activate
python3 ~/frame_test.py --email        # Capture + infer + email annotated frame
python3 ~/frame_test.py --benchmark    # 10-iteration inference timing
```

**In-engine test** (engine running — no camera conflict):
Set `TEST_EMAIL=1` in `~/rook-env/.env` and restart the service. Sends a live frame immediately on startup, then resumes normal operation. Remove the flag after use.

**Performance evaluation** (run from Mac or Pi after pulling `rook.log`):
```bash
python3 rook_eval.py                  # reads ~/rook.log, writes rook_eval_report.md
python3 rook_eval.py /path/to/rook.log --json   # explicit path + JSON output
```
Produces a report covering detection volume, alert rate, fixture suppression, ghost-motion gate efficiency, thermal behavior, and automated recommendations.

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

Unclassified motion frames and Beast Cam wildlife crops are synced from the Pi to Dropbox every 15 minutes via crontab:

```
*/15 * * * * /path/to/rook-sensor/app/sync_archive.sh >> /tmp/rook_sync.log 2>&1
```

Frames are saved at 640×360 (YOLO training resolution) to minimize SD card I/O. The Pi auto-purges Beast Cam directories older than 7 days.

**Manual export + clear** (e.g., after a thermal shutdown with no sync since last pull):
```bash
# Pull everything accumulated on the Pi
bash rook-sensor/app/sync_archive.sh

# After confirming sync, clear the Pi to free disk space
ssh rook@rook.local "find ~/rook-archive/unclassified/ -name '*.jpg' -mtime +1 -delete"
ssh rook@rook.local "find ~/beast_cam/ -name '*.jpg' -mtime +1 -delete"
ssh rook@rook.local "df -h ~"   # verify space freed
```

---

## License

MIT — see [LICENSE](LICENSE).
