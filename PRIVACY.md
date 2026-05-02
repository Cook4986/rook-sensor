# Privacy Policy

**Rook — Privacy-First Ambient Monitor**
*Last updated: May 2, 2026*

---

## What Rook Is

Rook is a personal, single-user IoT camera system that runs on a Raspberry Pi 5. It uses edge AI (YOLOv11n) to detect objects and activity in its field of view, translates detections into emoji-based summaries, and sends SMS notifications to the device owner.

## Core Privacy Commitment

**No video is ever recorded, saved, or transmitted.**

Rook processes camera frames in real-time on the device itself (edge inference). Frames are discarded from memory immediately after processing. The only outputs are:

- **Emoji-based text notifications** (e.g., `📦🚚`, `🦌`) sent via SMS to the device owner
- **Anonymized metadata** (timestamp, detection class, confidence score) optionally logged for the device owner's personal review
- **Emergency frames** (🚨 events only) may be temporarily stored with a 24-hour auto-deletion policy, accessible only to the device owner via authenticated, pre-signed URLs

## Data Collection

### What We Do NOT Collect
- Raw video or image streams
- Biometric data or facial recognition data
- Personal information about individuals in the camera's field of view
- Location data beyond what the device owner configures
- Any data from third parties

### What the Device Processes Locally
- Camera frames at 640×480 resolution, processed in volatile memory (RAM) and immediately discarded
- Object detection results (class labels like "person", "car", "dog" — no identification of individuals)
- Background subtraction motion data (pixel-level, never stored)

### What Is Transmitted
- SMS/MMS messages containing emoji summaries and detection counts — sent only to the device owner's verified phone number via Twilio
- Optional: anonymized event metadata (timestamp, detection class, confidence score) to a Supabase database controlled by the device owner

## Data Storage

- **On-device:** No persistent image or video storage. The SD card contains only the operating system, application code, and configuration files. `/tmp` and `/var/log` are mounted as RAM disks (tmpfs) and are cleared on every reboot.
- **Cloud (optional):** If the device owner enables the dashboard, anonymized event metadata is stored in a Supabase Postgres database under the owner's account, protected by Row-Level Security (RLS). Emergency frames, if captured, are stored in Cloudflare R2 with a 24-hour TTL and accessed only via authenticated pre-signed URLs.

## SMS Messaging

- Rook sends SMS notifications exclusively to the phone number configured by the device owner during setup.
- Messages are sent via Twilio's API using a registered A2P 10DLC campaign.
- **No marketing messages are ever sent.**
- **No third-party phone numbers are contacted.**
- The device owner can stop notifications at any time by powering off the device, removing their phone number from the configuration, or replying STOP to any message.

## Third-Party Services

| Service | Purpose | Data Shared |
|---------|---------|-------------|
| **Twilio** | SMS delivery | Message content (emoji text), recipient phone number |
| **Tailscale** | Secure remote access (VPN) | Device hostname, IP address (encrypted tunnel) |
| **Supabase** (optional) | Event logging dashboard | Anonymized detection metadata |
| **Cloudflare R2** (optional) | Emergency frame storage | Single frames with 24h auto-expiry |

**No mobile information will be shared with third parties/affiliates for marketing/promotional purposes.** All other categories exclude text messaging originator opt-in data and consent; this information will not be shared with any third parties. No data is sold, shared with advertisers, or used for any purpose beyond device operation.

## GDPR / CCPA Compliance

Rook is inherently privacy-compliant by design:

- No personally identifiable information (PII) is collected or stored
- No facial recognition or individual tracking is performed
- No raw imagery leaves the device
- The device owner has full control over all data and can delete it at any time

## Children's Privacy

Rook does not knowingly collect any information from or about children. The system does not identify, track, or profile individuals of any age.

## Changes to This Policy

This policy may be updated as the project evolves. Changes will be reflected in the repository commit history with an updated date above.

## Contact

This is a personal project. For questions, open an issue at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor).
