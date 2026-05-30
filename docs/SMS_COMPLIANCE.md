# Rook Sensor Alerts — SMS Opt-In Disclosure

**Twilio A2P 10DLC — Sole Proprietor Campaign**
*Brand: Rook · Operator: the device owner (sole proprietor)*
*This page is the publicly-verifiable opt-in disclosure for Rook's Twilio A2P 10DLC campaign registration.*

---

## Recipient model: single recipient, self-enrollment only

Rook is a personal-use, open-source IoT sensor. The **only** person eligible to receive SMS from a Rook deployment is the **device owner** — i.e., the sole proprietor who installs the software, provisions the Twilio sender, and runs the Raspberry Pi. The device owner and the SMS recipient are **the same individual**. There is no signup form, no mailing list, no API endpoint, and no other pathway through which any third-party number could be enrolled.

This is the self-recipient developer / IoT pattern described in [Twilio's A2P 10DLC quickstart](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/quickstart) ("*I am the only recipient of messages for my development testing suite. I ensure messages are only sent to my mobile number by opting my mobile number in within my system configuration file.*").

## How consent is collected (verifiable opt-in flow)

1. The device owner clones the public source repository at <https://github.com/Cook4986/rook-sensor>.
2. The device owner reads this disclosure page and the in-line disclosure shown in the README's setup section ([§4 Configure Environment](https://github.com/Cook4986/rook-sensor#4-configure-environment)).
3. On their own Raspberry Pi, the device owner edits `~/rook-env/.env` and writes their **own** mobile number into the `NOTIFY_TO_NUMBER` field. The disclosure shown immediately above that field (in this repo's README and in the file's own comments) names the program, message types, frequency, cost, opt-out, help, privacy, and terms.
4. The device owner enables the `rook.service` systemd unit. **Saving the configuration file and starting the service is the device owner's express written consent** to receive automated SMS from their own Rook device. No SMS is sent before this step.

This is the **only** opt-in path. There is no website signup form, no SMS keyword opt-in, no paper form, no verbal opt-in.

## Required A2P disclosures

| Field | Value |
|---|---|
| Program name | **Rook Sensor Alerts** |
| Brand type | Sole Proprietor (single device owner = sole recipient) |
| Message types | Emoji-based yard-activity alerts (e.g. `📦🚚`, `🦅`), thermal warnings, system status |
| Marketing? | **No.** Marketing messages are never sent. |
| Embedded links / phone numbers? | **No.** |
| Message frequency | Variable, activity-driven. Typically **0–20 messages/day**. |
| Cost | **Message and data rates may apply** (per the device owner's mobile carrier). |
| Opt-out keywords | STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT (Twilio default) |
| Opt-out (additional) | Remove `NOTIFY_TO_NUMBER` from `~/rook-env/.env` and restart the service, or power off the device. |
| Help keywords | HELP, INFO (Twilio default) |
| Third-party sharing | **None.** Mobile numbers are not shared with third parties or affiliates for marketing or promotional purposes. |
| Privacy Policy | <https://github.com/Cook4986/rook-sensor/blob/main/PRIVACY.md> |
| Terms & Conditions | <https://github.com/Cook4986/rook-sensor/blob/main/TERMS.md> |

## Sample messages

> Rook Sensor Alerts: 📦🚚 Delivery activity detected at front of building. Reply STOP to opt out, HELP for help. Msg&data rates may apply.

> Rook Sensor Alerts: ⚠️ Thermal warning — Pi SoC at 75°C, monitor closely. Reply STOP to opt out.

> Rook Sensor Alerts: 📷 FRAME test — YOLO detected: [person, car, dog]. Reply STOP to opt out.

---

*Last updated: May 24, 2026. This page is referenced from the Twilio Console campaign registration `Message Flow` field for the Rook Sole Proprietor campaign.*
