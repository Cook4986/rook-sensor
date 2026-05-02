import os
import time
import cv2
import smtplib
from email.message import EmailMessage
import mimetypes
from datetime import datetime, timezone
from dotenv import load_dotenv
from picamera2 import Picamera2
from ultralytics import YOLO
from suntime import Sun

# Load Environment Variables
load_dotenv(os.path.expanduser("~/rook-env/.env"))

# Constants & Tunables
MOTION_THRESHOLD_PIXELS = 3000  # How many pixels must change to trigger YOLO
COOLDOWN_SECONDS = 60           # Minimum seconds between alerts
QUIET_HOURS_START = 23          # 11 PM
QUIET_HOURS_END = 6             # 6 AM

# Base YOLO COCO to Emoji map
EMOJI_MAP = {
    "person": "🚶", "backpack": "🎒", "umbrella": "☂️",
    "bicycle": "🚲", "car": "🚗", "motorcycle": "🏍️",
    "bus": "🚌", "truck": "🚚",
    "dog": "🐕", "cat": "🐈", "bird": "🦅",
    "bear": "🐻", "horse": "🐎", "sheep": "🐑", "cow": "🐄"
}

def translate_to_emoji_summary(detected_classes):
    """
    Applies heuristics to raw YOLO classes to match the rich Rook Emoji Vocabulary.
    Examples:
      - person + dog = Dog Walker 🐕🦺
      - >3 persons = Crowd 🏟️
      - truck = Delivery/Sanitation 📦🚚
    """
    summary = []
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}
    
    # 1. Neighborhood Patterns
    if counts.get("person", 0) > 3:
        summary.append("🏟️")
    elif counts.get("person", 0) > 1:
        summary.append("👥")
        
    if "person" in counts and "dog" in counts:
        summary.append("🐕🚶")
        counts["person"] = 0 # Consume the person
        counts["dog"] = 0    # Consume the dog
        
    # 2. Routine Logistics
    if "truck" in counts:
        summary.append("🚚")
        counts["truck"] = 0
    if "bus" in counts:
        summary.append("🚌")
        counts["bus"] = 0
        
    # 3. Leftovers
    for obj, count in counts.items():
        if count > 0:
            emoji = EMOJI_MAP.get(obj, f"[{obj}]")
            summary.append(f"{emoji} x{count}")
            
    return " ".join(summary)

def is_daytime():
    """Mathematical sun position for dynamic exposure limits."""
    lat = float(os.environ.get("LATITUDE", "42.37"))
    lon = float(os.environ.get("LONGITUDE", "-71.11"))
    sun = Sun(lat, lon)
    now = datetime.now(timezone.utc)
    try:
        return sun.get_sunrise_time() < now < sun.get_sunset_time()
    except:
        hour = datetime.now().hour
        return 6 <= hour <= 18

def is_quiet_hours():
    """Check if current time is within suppressed quiet hours."""
    hour = datetime.now().hour
    if QUIET_HOURS_START <= QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    else: # Handles overnight wraps (e.g. 23 to 6)
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END

def configure_camera_exposure(cam):
    """Sets camera EV and limits based on day/night."""
    if is_daytime():
        cam.set_controls({"ExposureValue": 0.0, "FrameDurationLimits": (33333, 33333)})
        print("☀️  Camera locked to Daytime Exposure")
    else:
        cam.set_controls({"ExposureValue": 1.0, "FrameDurationLimits": (33333, 100000)})
        print("🌙 Camera locked to Nighttime Exposure")

def send_email_alert(emoji_summary, image_path):
    """Dispatch alert via SMTP fallback."""
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        notify_email = os.environ.get("NOTIFY_EMAIL")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            print("⚠️  Skipping Alert — Missing SMTP credentials.")
            return

        msg = EmailMessage()
        msg["Subject"] = f"Rook Alert: {emoji_summary}"
        msg["From"] = smtp_user
        msg["To"] = notify_email
        msg.set_content(f"Rook detected activity:\n\n{emoji_summary}")

        if os.path.exists(image_path):
            ctype, encoding = mimetypes.guess_type(image_path)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            with open(image_path, "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename="alert.jpg")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            
        print(f"📧 Alert dispatched to {notify_email}")
    except Exception as e:
        print(f"❌ Failed to send alert: {e}")

def main():
    print("🚀 Initializing Rook Engine...")
    
    # 1. Boot YOLO
    print("🧠 Loading YOLOv11n...")
    model = YOLO("yolo11n.pt")
    
    # 2. Boot Camera in Video Mode
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()
    configure_camera_exposure(cam)
    
    # 3. Initialize MOG2 Subtractor
    # history=500 frames, varThreshold=50 (higher = less sensitive to noise)
    mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    
    last_alert_time = 0
    flip_180 = os.environ.get("FLIP_180", "1") == "1"
    
    print("🛡️ Rook is armed and watching...")
    
    try:
        while True:
            # Capture frame
            frame = cam.capture_array()
            
            # Drop alpha channel if Picamera2 returns XBGR8888 (4 channels)
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]
                
            if flip_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            
            # Downscale frame purely for motion detection to save CPU
            small_frame = cv2.resize(frame, (640, 360))
            fgmask = mog.apply(small_frame)
            
            # Calculate amount of motion
            motion_pixels = cv2.countNonZero(fgmask)
            
            if motion_pixels > MOTION_THRESHOLD_PIXELS:
                now = time.time()
                
                # Check rate limit cooldown
                if now - last_alert_time > COOLDOWN_SECONDS:
                    print(f"🚨 Motion Detected ({motion_pixels} px)! Running YOLO...")
                    
                    # Run YOLO on the full-res frame
                    results = model(frame, imgsz=640, conf=0.45, verbose=False)
                    
                    # Extract all classes detected (with duplicates for counting)
                    detected_classes = [results[0].names[int(c)] for c in results[0].boxes.cls]
                    
                    if not detected_classes:
                        print("   Ghost motion (no objects found). Ignored.")
                        continue
                        
                    # Translate to rich emoji summary
                    emojis = translate_to_emoji_summary(detected_classes)
                    print(f"   Identified: {emojis}")
                    
                    # Save annotated image
                    annotated = results[0].plot()
                    out_path = "/tmp/rook_alert.jpg"
                    cv2.imwrite(out_path, annotated)
                    
                    # Dispatch logic
                    if not is_quiet_hours():
                        send_email_alert(emojis, out_path)
                    else:
                        print(f"   🔕 Quiet hours active. Alert suppressed: {emojis}")
                        
                    last_alert_time = time.time()
                else:
                    # Motion detected, but we are in cooldown. Just update the MOG2 background state.
                    pass
            
            # Small sleep to prevent 100% CPU pinning on the MOG loop
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down Rook Engine...")
    finally:
        cam.stop()
        cam.close()

if __name__ == "__main__":
    main()
