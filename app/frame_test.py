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


def capture_frame():
    """Capture a single 1080p frame from the Arducam B0444."""
    cam = Picamera2()
    cam.configure(cam.create_still_configuration(main={"size": (1920, 1080)}))
    cam.start()
    time.sleep(2)  # Let auto-exposure settle
    frame = cam.capture_array()
    cam.stop()
    cam.close()
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


if __name__ == "__main__":
    main()
