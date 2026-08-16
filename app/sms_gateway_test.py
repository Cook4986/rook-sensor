#!/usr/bin/env python3
"""Standalone test for the carrier email-to-SMS/MMS gateway (Road A, Option 1).

Sends a test alert to a US mobile number via its carrier's email gateway,
reusing the same Gmail SMTP credentials the engine already uses. This does NOT
touch Twilio / A2P 10DLC — it is plain email addressed to a carrier gateway,
which the carrier converts into a text delivered to the native Messages app.

Usage:
    # Reads SMTP_* from ~/rook-env/.env or the environment.
    python3 sms_gateway_test.py --number 5551234567 --carrier verizon
    python3 sms_gateway_test.py --number 5551234567 --carrier verizon --image ../assets/sample.jpg

Env overrides (if not already in ~/rook-env/.env):
    SMTP_SERVER (default smtp.gmail.com)
    SMTP_PORT   (default 587)
    SMTP_USER   (required — the sending Gmail/Workspace address)
    SMTP_PASS   (required — the 16-char Gmail app password)
"""
import argparse
import mimetypes
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/rook-env/.env"))
except Exception:
    pass

# Carrier gateways. "sms" = text-only (~160 chars, no images);
# "mms" = supports a subject line, longer text, emoji, and image attachments.
GATEWAYS = {
    "verizon":  {"sms": "vtext.com",     "mms": "vzwpix.com"},
    "att":      {"sms": "txt.att.net",   "mms": "mms.att.net"},
    "tmobile":  {"sms": "tmomail.net",   "mms": "tmomail.net"},
    "googlefi": {"sms": "msg.fi.google.com", "mms": "msg.fi.google.com"},
}


def send(recipient, subject, body, smtp, image_path=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp["user"]
    msg["To"] = recipient
    msg.set_content(body)

    if image_path and os.path.exists(image_path):
        ctype, _ = mimetypes.guess_type(image_path)
        maintype, subtype = (ctype or "image/jpeg").split("/", 1)
        with open(image_path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(image_path))

    with smtplib.SMTP(smtp["server"], smtp["port"]) as server:
        server.starttls()
        server.login(smtp["user"], smtp["pass"])
        server.send_message(msg)


def main():
    ap = argparse.ArgumentParser(description="Test carrier email-to-SMS/MMS gateway.")
    ap.add_argument("--number", required=True, help="10-digit US mobile number, e.g. 5551234567")
    ap.add_argument("--carrier", default="verizon", choices=sorted(GATEWAYS))
    ap.add_argument("--image", help="Optional image path to test MMS image delivery")
    ap.add_argument("--mode", default="both", choices=["sms", "mms", "both"],
                    help="Which gateway(s) to test (default: both)")
    args = ap.parse_args()

    digits = "".join(ch for ch in args.number if ch.isdigit())[-10:]
    if len(digits) != 10:
        sys.exit(f"❌ Expected a 10-digit US number, got: {args.number!r}")

    smtp = {
        "server": os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", 587)),
        "user": os.environ.get("SMTP_USER"),
        "pass": os.environ.get("SMTP_PASS"),
    }
    if not smtp["user"] or not smtp["pass"]:
        sys.exit("❌ Missing SMTP_USER and/or SMTP_PASS (set them in ~/rook-env/.env or the environment).")

    gw = GATEWAYS[args.carrier]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    targets = []
    if args.mode in ("sms", "both"):
        targets.append(("SMS ", f"{digits}@{gw['sms']}", False))
    if args.mode in ("mms", "both"):
        targets.append(("MMS ", f"{digits}@{gw['mms']}", True))

    for label, recipient, allow_image in targets:
        subject = "Rook Sensor Alerts"
        body = (f"Rook Sensor Alerts: \U0001F4E6\U0001F69A Test alert ({label.strip()}) "
                f"at {now}. Reply STOP to opt out, HELP for help. Msg&data rates may apply.")
        try:
            send(recipient, subject, body, smtp,
                 image_path=args.image if allow_image else None)
            print(f"✅ {label}sent to {recipient}"
                  + (f" with image {args.image}" if (allow_image and args.image) else ""))
        except Exception as e:
            print(f"❌ {label}failed to {recipient}: {e}")

    print("\nWatch your phone's Messages app. The MMS version (vzwpix.com) carries "
          "emoji + the image; the SMS version (vtext.com) is plain text only.")


if __name__ == "__main__":
    main()
