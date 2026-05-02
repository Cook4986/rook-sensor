import os
import time
import cv2
import smtplib
from email.message import EmailMessage
import mimetypes
from datetime import datetime, timezone
import logging
from dotenv import load_dotenv
from picamera2 import Picamera2
from ultralytics import YOLO
from suntime import Sun
import httpx

# Load Environment Variables
load_dotenv(os.path.expanduser("~/rook-env/.env"))

# Constants & Tunables
MOTION_THRESHOLD_PIXELS = 3000  # How many pixels must change to trigger YOLO
COOLDOWN_SECONDS = 60           # Minimum seconds between alerts
QUIET_HOURS_START = 23          # 11 PM
QUIET_HOURS_END = 6             # 6 AM
MIN_ALERT_SCORE = 15            # Minimum score to trigger real-time Email/Slack
LOG_FILE = os.path.expanduser("~/rook.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler() # Also print to console
    ]
)

# Base YOLO COCO to Emoji map
EMOJI_MAP = {
    "person": "🚶", "backpack": "🎒", "umbrella": "☂️",
    "bicycle": "🚲", "car": "🚗", "motorcycle": "🏍️",
    "bus": "🚌", "truck": "🚚",
    "bear": "🐻", "horse": "🐎", "sheep": "🐑", "cow": "🐄"
}

# Base Scores for Anomalies
SCORE_MAP = {
    "person": 1, "car": 1, "dog": 2, "bicycle": 2,
    "truck": 5, "bus": 5, "motorcycle": 3,
    "cat": 10, "bird": 15,
    "bear": 100, "horse": 50, "sheep": 50, "cow": 50
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

def calculate_image_score(detected_classes):
    """Assigns a rarity score to an image based on the YOLO detections."""
    score = 0
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}
    
    for obj, count in counts.items():
        base = SCORE_MAP.get(obj, 1)
        # Multiplier for multiple objects of the same type (e.g. 3 dogs = rarer than 1)
        score += base * (count ** 1.5)
        
    # Bonus for complex scenes (many different types of objects)
    score += len(counts) * 5
    return int(score)

def configure_camera_exposure(cam):
    """Sets camera EV and limits based on day/night."""
    if is_daytime():
        cam.set_controls({"ExposureValue": 0.0, "FrameDurationLimits": (33333, 33333)})
        logging.info("☀️  Camera locked to Daytime Exposure")
    else:
        cam.set_controls({"ExposureValue": 1.0, "FrameDurationLimits": (33333, 100000)})
        logging.info("🌙 Camera locked to Nighttime Exposure")

def send_daily_digest(notify_email, best_image_data):
    """Compiles the daily log file and emails it at 6 PM, including the most interesting photo."""
    try:
        logging.info("Generating daily 6 PM digest...")
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        
        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            logging.error("Missing SMTP credentials for daily digest.")
            return

        with open(LOG_FILE, "r") as f:
            logs = f.read()

        msg = EmailMessage()
        msg["Subject"] = f"Rook Daily Digest - {datetime.now().strftime('%Y-%m-%d')}"
        msg["From"] = smtp_user
        msg["To"] = notify_email
        
        body = f"Rook System Logs for the day:\n\n{logs}"
        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            body = f"🏆 Top Activity of the Day: {best_image_data['summary']} (Score: {best_image_data['score']})\n\n" + body
            
        msg.set_content(body)

        # Attach the best image if we recorded one
        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            ctype, encoding = mimetypes.guess_type(best_image_data["path"])
            maintype, subtype = (ctype or "image/jpeg").split("/", 1)
            with open(best_image_data["path"], "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename="daily_highlight.jpg")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            
        logging.info(f"📧 Daily digest sent to {notify_email}")
        
        # Clear log after sending
        with open(LOG_FILE, "w") as f:
            f.write(f"--- Rook Log Reset ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
            
    except Exception as e:
        logging.error(f"❌ Failed to send daily digest: {e}")

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
        print(f"❌ Failed to send email alert: {e}")

def send_slack_alert(emoji_summary):
    """Dispatch real-time text alert via Slack Webhook."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
        
    try:
        payload = {"text": f"Rook Activity: {emoji_summary}"}
        httpx.post(webhook_url, json=payload, timeout=5.0)
        logging.info("💬 Slack alert sent!")
    except Exception as e:
        logging.error(f"❌ Failed to send Slack alert: {e}")

def main():
    logging.info("🚀 Initializing Rook Engine...")
    
    # 1. Boot YOLO
    logging.info("🧠 Loading YOLOv11n...")
    model = YOLO("yolo11n.pt")
    
    # 2. Boot Camera in Video Mode
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()
    configure_camera_exposure(cam)
    
    # 3. Initialize MOG2 Subtractor
    mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    
    last_alert_time = 0
    last_detected_classes = []  # State tracking for redundancy (parked cars)
    flip_180 = os.environ.get("FLIP_180", "1") == "1"
    last_daytime_check = time.time()
    last_digest_date = None
    
    # State for the daily "best image" competition
    best_daily_image = {"score": 0, "path": None, "summary": ""}
    
    logging.info("🛡️ Rook is armed and watching...")
    
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
            
            # Daily Digest Trigger (6 PM / 18:00)
            now_hour = datetime.now().hour
            today_date = datetime.now().strftime('%Y-%m-%d')
            if now_hour == 18 and last_digest_date != today_date:
                send_daily_digest(os.environ.get("NOTIFY_EMAIL"), best_daily_image)
                last_digest_date = today_date
                # Reset best image for the new day
                best_daily_image = {"score": 0, "path": None, "summary": ""}
                
            # Periodically re-evaluate day/night exposure (every 10 mins)
            if time.time() - last_daytime_check > 600:
                configure_camera_exposure(cam)
                last_daytime_check = time.time()
            
            if motion_pixels > MOTION_THRESHOLD_PIXELS:
                now = time.time()
                
                # Check rate limit cooldown
                if now - last_alert_time > COOLDOWN_SECONDS:
                    logging.info(f"🚨 Motion Detected ({motion_pixels} px)! Running YOLO...")
                    
                    # Run YOLO on the full-res frame at intermediate resolution (800px)
                    results = model(frame, imgsz=800, conf=0.45, verbose=False)
                    
                    # Extract all classes detected (with duplicates for counting)
                    detected_classes = [results[0].names[int(c)] for c in results[0].boxes.cls]
                    
                    if not detected_classes:
                        logging.info("   Ghost motion (no objects found). Ignored.")
                        continue
                        
                    # State check: prevent redundant alerts for parked cars/lingering objects
                    if sorted(detected_classes) == sorted(last_detected_classes):
                        logging.info("   Redundant objects (no change in scene). Ignored.")
                        # Reset cooldown so we don't alert the moment the cooldown expires if they are still there
                        last_alert_time = time.time()
                        continue
                        
                    last_detected_classes = detected_classes
                    
                    # Translate to rich emoji summary
                    emojis = translate_to_emoji_summary(detected_classes)
                    logging.info(f"   Identified: {emojis}")
                    
                    # Save annotated image
                    annotated = results[0].plot()
                    out_path = "/tmp/rook_alert.jpg"
                    cv2.imwrite(out_path, annotated)
                    
                    # Score image for the daily digest competition
                    img_score = calculate_image_score(detected_classes)
                    if img_score > best_daily_image["score"]:
                        best_path = "/tmp/rook_best_daily.jpg"
                        cv2.imwrite(best_path, annotated)
                        best_daily_image = {"score": img_score, "path": best_path, "summary": emojis}
                        logging.info(f"   🏆 New Daily High Score: {img_score}!")
                    
                    # Dispatch logic: Only alert in real-time if it's a high-ranked event
                    if img_score >= MIN_ALERT_SCORE:
                        if not is_quiet_hours():
                            send_email_alert(emojis, out_path)
                            send_slack_alert(emojis)
                        else:
                            logging.info(f"   🔕 Quiet hours active. Alert suppressed: {emojis}")
                    else:
                        logging.info(f"   📉 Routine event (Score: {img_score}). Confined to daily digest.")
                        
                    last_alert_time = time.time()
                else:
                    # Motion detected, but we are in cooldown. Just update the MOG2 background state.
                    pass
            
            # Small sleep to prevent 100% CPU pinning on the MOG loop
            time.sleep(0.1)

    except KeyboardInterrupt:
        logging.info("\n🛑 Shutting down Rook Engine...")
    finally:
        cam.stop()
        cam.close()

if __name__ == "__main__":
    main()
