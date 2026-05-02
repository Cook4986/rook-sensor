#!/usr/bin/env python3
"""
Rook — FRAME Test & YOLO Benchmark
====================================
Captures a frame from the Arducam B0444, runs YOLOv11n inference,
sends an SMS with detection results, and prints benchmark timing.

Usage (on the Pi):
    source ~/rook-env/bin/activate
    python3 frame_test.py [--benchmark] [--sms]

Flags:
    --benchmark   Run 10-iteration inference benchmark (default: off)
    --sms         Send SMS via Twilio with detection summary (default: off)
    (no flags)    Capture + infer + save annotated image to /tmp/rook_frame.jpg
"""

import os
import sys
import time
import argparse

from dotenv import load_dotenv
from picamera2 import Picamera2
from ultralytics import YOLO
import cv2
import smtplib
from email.message import EmailMessage
import mimetypes


def capture_frame():
    """Capture a single 1080p frame from the Arducam B0444."""
    cam = Picamera2()
    cam.configure(cam.create_still_configuration(main={"size": (1920, 1080)}))
    cam.start()
    time.sleep(2)  # Let auto-exposure settle
    frame = cam.capture_array()
    cam.stop()
    cam.close()
    
    # Software 180-degree rotation (default True for current physical mount)
    if os.environ.get("FLIP_180", "1") == "1":
        frame = cv2.rotate(frame, cv2.ROTATE_180)
        
    return frame


def run_inference(model, frame):
    """Run YOLOv11n inference on a frame."""
    return model(frame, imgsz=640, conf=0.45, verbose=False)


def run_benchmark(model, frame, iterations=10):
    """Benchmark YOLOv11n inference timing."""
    # Warm-up
    run_inference(model, frame)

    times = []
    for _ in range(iterations):
        t0 = time.time()
        run_inference(model, frame)
        times.append((time.time() - t0) * 1000)

    avg = sum(times) / len(times)
    print(f"\n{'═' * 50}")
    print(f"  YOLOv11n Benchmark @ 640px ({iterations} iterations)")
    print(f"  Avg: {avg:.0f}ms | Min: {min(times):.0f}ms | Max: {max(times):.0f}ms")
    target = "✅ PASS" if avg < 100 else "⚠️  ABOVE 100ms TARGET"
    print(f"  Status: {target}")
    print(f"{'═' * 50}\n")
    return avg


def send_sms(detections_text):
    """Send an SMS via Twilio with detection results."""
    from twilio.rest import Client

    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )
    msg = client.messages.create(
        body=f"📷 Rook FRAME test — YOLO detected: {detections_text}",
        from_=os.environ["TWILIO_FROM_NUMBER"],
        to=os.environ["NOTIFY_TO_NUMBER"],
    )
    print(f"✅ SMS sent: {msg.sid}")


def send_email(detections_text, image_path):
    """Send an email via SMTP with detection results and attached image."""
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        notify_email = os.environ.get("NOTIFY_EMAIL")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            print("⚠️  Skipping Email — Missing SMTP credentials in ~/rook-env/.env")
            return

        msg = EmailMessage()
        msg["Subject"] = "📷 Rook FRAME Test Results"
        msg["From"] = smtp_user
        msg["To"] = notify_email
        msg.set_content(f"Rook FRAME test complete.\n\nYOLO detected: {detections_text}\n\nSee attached photo for camera placement and focus verification.")

        # Attach image
        if os.path.exists(image_path):
            ctype, encoding = mimetypes.guess_type(image_path)
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            
            with open(image_path, "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(image_path))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            
        print(f"✅ Email sent to {notify_email} with attached photo.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


def get_thermal():
    """Read the Pi's CPU temperature."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_c = int(f.read().strip()) / 1000
        return temp_c
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Rook FRAME test & benchmark")
    parser.add_argument("--benchmark", action="store_true", help="Run 10-iteration inference benchmark")
    parser.add_argument("--sms", action="store_true", help="Send SMS with detection results")
    parser.add_argument("--email", action="store_true", help="Send email with detection results and photo attachment")
    args = parser.parse_args()

    # Load environment
    env_path = os.path.expanduser("~/rook-env/.env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    print("📷 Capturing frame...")
    frame = capture_frame()

    print("🧠 Loading YOLOv11n...")
    model = YOLO("yolo11n.pt")

    print("🔍 Running inference...")
    results = run_inference(model, frame)

    # Extract detections
    detection_names = [results[0].names[int(c)] for c in results[0].boxes.cls]
    detections_text = ", ".join(detection_names) if detection_names else "nothing (empty scene)"
    print(f"   Detected: {detections_text}")

    # Save annotated image
    annotated = results[0].plot()
    out_path = "/tmp/rook_frame.jpg"
    cv2.imwrite(out_path, annotated)
    print(f"💾 Annotated frame saved: {out_path}")

    # Thermal reading
    temp = get_thermal()
    if temp is not None:
        emoji = "🟢" if temp < 60 else ("🟡" if temp < 75 else "🔴")
        print(f"🌡️  CPU temp: {temp:.1f}°C {emoji}")

    # Optional benchmark
    if args.benchmark:
        run_benchmark(model, frame)

    # Optional SMS
    if args.sms:
        if not os.environ.get("TWILIO_ACCOUNT_SID"):
            print("⚠️  Skipping SMS — TWILIO_ACCOUNT_SID not set. Edit ~/rook-env/.env")
        else:
            send_sms(detections_text)

    # Optional Email
    if args.email:
        send_email(detections_text, out_path)


if __name__ == "__main__":
    main()
