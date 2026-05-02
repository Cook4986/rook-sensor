# Terms and Conditions

**Rook — Privacy-First Ambient Monitor**
*Last updated: May 2, 2026*

---

## 1. Overview

Rook is an open-source, personal-use IoT monitoring system that uses edge AI to detect activity in a camera's field of view and deliver emoji-based SMS notifications to the device owner. These terms govern the use of the Rook software and associated notification services.

## 2. Intended Use

Rook is designed for **personal, residential use** by the device owner. It is intended for ambient awareness of publicly visible areas (streets, sidewalks, parks) from a private window installation.

**Rook is NOT intended for:**
- Surveillance of private spaces or individuals
- Law enforcement or evidence gathering
- Commercial security monitoring
- Any use that violates local, state, or federal privacy laws

The device owner is solely responsible for ensuring their use of Rook complies with all applicable laws regarding camera placement and monitoring in their jurisdiction.

## 3. SMS Notifications

### Message Program
- **Program name:** Rook Sensor Alerts
- **Message frequency:** Variable; determined by detected activity. Typically 0–20 messages per day during active hours. Emergency alerts (🚨) bypass rate limits.
- **Message content:** Emoji-based activity summaries, system status alerts, and thermal warnings.
- **Quiet hours:** Routine notifications are suppressed between 11 PM and 6 AM by default (configurable). Emergency alerts remain active 24/7.

### Opting Out
You can stop receiving messages at any time by:
- Replying **STOP** to any Rook message
- Removing your phone number from the device configuration
- Powering off the Rook device

Reply **HELP** for support information.

### Message & Data Rates
Standard message and data rates from your mobile carrier may apply.

### Support
For support, open an issue at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor) or contact the device owner directly.

## 4. No Warranty

Rook is provided **"as is"** without warranty of any kind, express or implied. The authors and contributors are not liable for any claim, damages, or other liability arising from the use of the software, including but not limited to:

- Missed or delayed notifications
- False positive or false negative detections
- Hardware damage, SD card wear, or thermal events
- Any consequences of acting or failing to act on Rook notifications

## 5. Privacy

Rook is designed with privacy as a core principle. No video is recorded or transmitted. See the [Privacy Policy](PRIVACY.md) for full details.

## 6. Open Source

Rook is open-source software released under the [MIT License](LICENSE). The source code, hardware documentation, and design rationale are publicly available at [github.com/Cook4986/rook-sensor](https://github.com/Cook4986/rook-sensor). Contributions and forks are welcome.

## 7. Modifications

These terms may be updated as the project evolves. Changes will be reflected in the repository commit history with an updated date above.
