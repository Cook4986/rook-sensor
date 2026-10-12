# Privacy Policy

**Rook — Privacy-First Ambient Yard Monitor**
*Last updated: October 12, 2026*

---

## What Rook Is

Rook is a personal, single-user IoT camera system that runs on a Raspberry Pi 5. It uses edge AI (YOLO26n) to detect objects and activity in its field of view, translates detections into emoji-based summaries, and delivers notifications to the device owner via Slack and email. SMS is a retired implementation path; its historical program terms remain below.

## Core Privacy Commitment

**No video is ever recorded, saved, or transmitted.**

Rook processes camera frames in real-time on the device itself (edge inference). Live inference uses volatile memory, but selected still frames are retained in the
training archive and wildlife digest cache. Outputs include:

- **Emoji-based notifications** (e.g., `📦🚚`, `🦅`) sent to the device owner via Slack and/or email
- **Event metadata** (timestamp, detection class, confidence score) optionally logged locally for the device owner's personal review
- **Archived still frames** from classified and unclassified activity, synced to the owner's private archive for review and model development
- **Wildlife crops** (Beast Cam) temporarily cached on-device and sent to the device owner in the nightly digest, then deleted immediately after confirmed delivery

## Data Collection

### What We Do NOT Collect
- Continuous video recordings
- Biometric data or facial recognition data
- Identity labels for individuals; however, retained still images may depict identifiable people
- Location data beyond what the device owner explicitly configures
- Any data from third parties

### What the Device Processes Locally
- Camera frames for live inference; selected still frames and wildlife crops may be retained
- Motion detection data via background subtraction (pixel-level, never stored)
- Object detection results (class labels such as "person", "car", "bird" — no identification of individuals)

### What Is Transmitted
- Emoji-based alert messages sent to the device owner via Slack webhook and/or SMTP email
- Legacy SMS support via Twilio is no longer the active notification plan
- Nightly digest email including activity summary and wildlife crop images, sent only to the device owner
- Optional archive labeling requests to an owner-configured vision service, described below
- Once explicitly configured and deployed, event metadata and selected raw JPEGs to the owner's private dashboard; no dashboard uploads are enabled by default

## SMS Notifications (Twilio)

The following describes the historical optional SMS path. SMS is not being enabled
as part of the private dashboard pilot.

### Opt-In
The device owner opts in by entering their own phone number in the device configuration file (`~/rook-env/.env`) during initial setup. No other individuals are enrolled, and no unsolicited messages are sent.

### Message Program
- **Program name:** Rook Sensor Alerts
- **Message frequency:** Variable based on detected activity. Typically 0–20 messages per day during active hours. Emergency alerts bypass rate limits.
- **Message content:** Emoji-based activity summaries (e.g., `🦅`, `📦🚚`), system status, and thermal warnings. **No marketing messages are ever sent.**

### Opt-Out
Reply **STOP** to any message to stop receiving SMS notifications. You may also remove your phone number from the device configuration or power off the device.

Reply **HELP** for support contact information.

### Message & Data Rates
Standard message and data rates from your mobile carrier may apply. Carriers are not liable for delayed or undelivered messages.

### No Third-Party Sharing
**No mobile information will be shared with third parties or affiliates for marketing or promotional purposes.** All other categories exclude text messaging originator opt-in data and consent; this information will not be shared with any third parties. No data is sold, shared with advertisers, or used for any purpose beyond device operation.

## Data Storage

- **On-device:** No video storage. The SD card contains the OS, application code, configuration, and a rate-limited training archive of still frames (`~/rook-archive/`): motion frames YOLO could not classify, and event-sampled frames with confirmed detections (640×360, with detection-class metadata). Archive frames are synced to the owner's private storage and deleted from the device within 7 days. `/tmp` and `/var/log` are RAM disks (tmpfs), cleared on every reboot.
- **Beast Cam cache:** Wildlife bounding-box crops are stored temporarily in `~/beast_cam/YYYY-MM-DD/` and deleted from the device immediately after the nightly digest email is confirmed sent.
- **Private dashboard (implemented locally, not hosted yet):** the opt-in pilot stores event metadata and raw JPEGs together in Supabase Postgres. Only the provisioned owner may read them through authenticated routes and row-level security. The initial quota is 1,000 events or 256 MiB of logical payload per device; there is no automatic cloud deletion yet. Retention and hosting costs must be settled before ongoing capture. Implementation: [private dashboard guide](docs/private_dashboard.md).

## Third-Party Services

| Service | Purpose | Data Shared |
|---------|---------|-------------|
| **Slack** | Real-time emoji alerts | Message text only (no images by default) |
| **SMTP / Gmail** | Email alerts + nightly digest | Message text + annotated detection image |
| **Twilio** (legacy) | SMS delivery | Message text, recipient phone number |
| **Open-Meteo** | Weather context (read-only API) | Device IP, GPS bounding box from configured coordinates |
| **iNaturalist API** | Local species context (read-only) | Configured latitude/longitude |
| **Tailscale** | Secure remote SSH (VPN) | Device hostname, encrypted IP tunnel |
| **Supabase + Vercel** (planned hosting for the opt-in dashboard) | Owner authentication, private event/image storage and serving | Owner account information, event metadata and selected raw JPEGs; server credentials remain server-side |
| **LLM API** (optional, owner-configured) | Offline auto-labeling of the owner's archive for model training | Image crops of detected objects (vehicles, wildlife, uniformed players) and whole archived frames (640×360) from the owner's own archive |

### LLM Auto-Labeling (optional, off-device)

The optional training pipeline (`app/llm_autolabel.py`) can send **cropped detections from the owner's own archive** to a vision LLM API the owner configures (e.g., OpenAI, or a fully local model via Ollama/vLLM) to assign fine-grained labels for model training. This tool:

- Runs **only on the owner's computer**, only when the owner invokes it — never on the Pi, never in the real-time detection path
- Is **opt-in**: paid labeling additionally requires explicit paid mode and a verified spend policy; configuring a key alone does not authorize paid inference
- Sends object crops (vehicles, wildlife) and, for frames where the detector found nothing, whole archived frames (640×360) — all from imagery the owner already possesses in their private archive
- Can be pointed at a **self-hosted LLM endpoint** for a fully offline workflow

Live detection remains on-device. Archived stills, digest images, optional labeling
requests and future private dashboard images are separate data flows; they are not
anonymous merely because they contain no identity labels.

## Owner Control

Rook does not perform facial recognition or name people in detections. Still images
can contain identifiable people, and selected imagery can leave the Pi through the
data flows above. The owner controls configuration and access to private archives.
The dashboard pilot has no deletion or retention interface yet. This technical
description does not certify compliance with privacy laws.

## Children's Privacy

Rook does not infer ages or identify people by name. Retained still frames can
incidentally include people of any age.

## Changes to This Policy

Changes will be reflected in the repository commit history with an updated date above.

## Contact

This is a personal project. For questions, open an issue at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor).
