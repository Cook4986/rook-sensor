# Terms and Conditions

**Rook — Privacy-First Ambient Yard Monitor**
*Last updated: May 2, 2026*

---

## 1. Overview

Rook is an open-source, personal-use IoT monitoring system that uses edge AI to detect activity in a camera's field of view and deliver emoji-based notifications to the device owner. These terms govern use of the Rook software and associated notification services.

## 2. Intended Use

Rook is designed for **personal, residential use** by the device owner. It is intended for ambient awareness of a private yard and publicly visible surroundings from an interior window installation.

**Rook is NOT intended for:**
- Surveillance of private spaces or individuals without consent
- Law enforcement or evidence gathering
- Commercial security monitoring
- Any use that violates local, state, or federal privacy laws

The device owner is solely responsible for ensuring their use of Rook complies with all applicable laws regarding camera placement and monitoring in their jurisdiction.

## 3. SMS Notifications (Twilio A2P)

### Opt-In

The device owner consents to receive SMS notifications by explicitly entering their own phone number in the device configuration file (`~/rook-env/.env`) during setup. This constitutes express written consent. No other individuals are enrolled. No unsolicited messages are sent.

### Message Program

- **Program name:** Rook Sensor Alerts
- **Message frequency:** Variable based on detected yard activity. Typically 0–20 messages per day during active hours. High-priority alerts (score ≥ 50) bypass quiet-hour suppression.
- **Message content:** Emoji-based activity summaries (e.g., `🦅`, `📦🚚`), system status, and thermal warnings.
- **No marketing messages are ever sent.** No third-party numbers are contacted.
- **Quiet hours:** Routine notifications suppressed 11 PM – 6 AM by default. Configurable.

### Opt-Out

Stop receiving messages at any time by:
- Replying **STOP** to any Rook message
- Removing your phone number from the device configuration (`NOTIFY_TO_NUMBER` in `.env`)
- Powering off the Rook device

Reply **HELP** to receive support contact information.

### Message & Data Rates

Standard message and data rates from your mobile carrier may apply. Carriers are not liable for delayed or undelivered messages.

### No Third-Party Sharing

No mobile information will be shared with third parties or affiliates for marketing or promotional purposes. Opt-in data and consent are not shared with any third parties.

## 4. No Warranty

Rook is provided **"as is"** without warranty of any kind, express or implied. The authors and contributors are not liable for any claim, damages, or other liability arising from the use of the software, including but not limited to:

- Missed or delayed notifications
- False positive or false negative detections
- Hardware damage, SD card wear, or thermal events
- Any consequences of acting or failing to act on Rook notifications

## 5. Privacy

Rook is designed with privacy as a core principle. No video is recorded or transmitted. See the [Privacy Policy](PRIVACY.md) for full details.

## 6. Open Source

Rook is open-source software released under the [MIT License](LICENSE). Source code, hardware documentation, and design rationale are publicly available at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor).

## 7. Modifications

These terms may be updated as the project evolves. Changes will be reflected in the repository commit history with an updated date above.
