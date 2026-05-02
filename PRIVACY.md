# Privacy Policy

**Rook — Privacy-First Ambient Yard Monitor**
*Last updated: May 2, 2026*

---

## What Rook Is

Rook is a personal, single-user IoT camera system that runs on a Raspberry Pi 5. It uses edge AI (YOLOv11n) to detect objects and activity in its field of view, translates detections into emoji-based summaries, and delivers notifications to the device owner via Slack, email, and optionally SMS.

## Core Privacy Commitment

**No video is ever recorded, saved, or transmitted.**

Rook processes camera frames in real-time on the device itself (edge inference). Frames are held in volatile memory during inference and immediately discarded. The only outputs are:

- **Emoji-based notifications** (e.g., `📦🚚`, `🦅`) sent to the device owner via Slack and/or email
- **Anonymized metadata** (timestamp, detection class, confidence score) optionally logged locally for the device owner's personal review
- **Wildlife crops** (Beast Cam) temporarily cached on-device and sent to the device owner in the nightly digest, then deleted immediately after confirmed delivery

## Data Collection

### What We Do NOT Collect
- Raw video or image streams
- Biometric data or facial recognition data
- Personal information about individuals in the camera's field of view
- Location data beyond what the device owner explicitly configures
- Any data from third parties

### What the Device Processes Locally
- Camera frames at 1920×1080, processed in volatile memory (RAM) and immediately discarded after inference
- Motion detection data via background subtraction (pixel-level, never stored)
- Object detection results (class labels such as "person", "car", "bird" — no identification of individuals)

### What Is Transmitted
- Emoji-based alert messages sent to the device owner via Slack webhook and/or SMTP email
- Optional SMS notifications via Twilio, sent only to the device owner's verified phone number (see SMS section below)
- Nightly digest email including activity summary and wildlife crop images, sent only to the device owner

## SMS Notifications (Twilio)

Rook may optionally send SMS notifications via Twilio's A2P 10DLC registered messaging service.

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

- **On-device:** No persistent image or video storage. The SD card contains only the OS, application code, and configuration. `/tmp` and `/var/log` are RAM disks (tmpfs), cleared on every reboot.
- **Beast Cam cache:** Wildlife bounding-box crops are stored temporarily in `~/beast_cam/YYYY-MM-DD/` and deleted from the device immediately after the nightly digest email is confirmed sent.
- **Cloud (optional, not yet implemented):** A future dashboard feature may store anonymized event metadata in a Supabase Postgres database under the owner's account.

## Third-Party Services

| Service | Purpose | Data Shared |
|---------|---------|-------------|
| **Slack** | Real-time emoji alerts | Message text only (no images by default) |
| **SMTP / Gmail** | Email alerts + nightly digest | Message text + annotated detection image |
| **Twilio** (optional) | SMS delivery | Message text, recipient phone number |
| **Open-Meteo** | Weather context (read-only API) | Device IP, GPS bounding box from configured coordinates |
| **iNaturalist API** | Local species context (read-only) | Configured latitude/longitude |
| **Tailscale** | Secure remote SSH (VPN) | Device hostname, encrypted IP tunnel |

## GDPR / CCPA

Rook is privacy-compliant by design:

- No PII is collected or stored
- No facial recognition or individual tracking is performed
- No raw imagery leaves the device
- The device owner has full control over all data and can delete it at any time

## Children's Privacy

Rook does not knowingly collect any information from or about children. The system does not identify, track, or profile individuals of any age.

## Changes to This Policy

Changes will be reflected in the repository commit history with an updated date above.

## Contact

This is a personal project. For questions, open an issue at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor).
