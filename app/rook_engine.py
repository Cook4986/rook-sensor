import os
import time
import cv2
import smtplib
import threading
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
MOTION_THRESHOLD_PIXELS = 500   # FIX #3: Lowered from 3000 — distant pedestrians occupy ~500px at 640x360
COOLDOWN_SECONDS = 60           # Minimum seconds between ALERTS (not inference)
QUIET_HOURS_START = 23          # 11 PM
QUIET_HOURS_END = 6             # 6 AM
MIN_EMAIL_SCORE = 15            # Score threshold for heavy Email/MMS alerts
MIN_SLACK_SCORE = 1             # Score threshold for lightweight real-time Slack pings
THERMAL_CHECK_INTERVAL = 30     # FIX #6: Check temp every 30s, not every 100ms frame
LOG_FILE = os.path.expanduser("~/rook.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# Base YOLO COCO to Emoji map
EMOJI_MAP = {
    "person": "🚶", "backpack": "🎒", "umbrella": "☂️", "suitcase": "🧳", "cell phone": "📱",
    "skateboard": "🛹", "sports ball": "⚽", "frisbee": "🥏", "kite": "🪁",
    "bicycle": "🚲", "car": "🚗", "motorcycle": "🏍️",
    "bus": "🚌", "truck": "🚚",
    "dog": "🐕", "cat": "🐈", "bird": "🦅",
    "bear": "🐻", "horse": "🐎", "sheep": "🐑", "cow": "🐄"
}

# Rarity scores — used for daily digest ranking and alert gating
SCORE_MAP = {
    "person": 1, "car": 1, "dog": 2, "bicycle": 2,
    "truck": 5, "bus": 5, "motorcycle": 3,
    "skateboard": 3, "sports ball": 3, "frisbee": 3, "kite": 5,
    "suitcase": 5, "cell phone": 5, "backpack": 2, "umbrella": 2,
    "cat": 10, "bird": 15,
    "bear": 100, "horse": 50, "sheep": 50, "cow": 50
}

def translate_to_emoji_summary(detected_classes):
    """
    Applies heuristics to raw YOLO classes.
    Defaults to single symbols, only adding composite clarification for anomalies (e.g. Loose Dog).
    """
    summary = []
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}

    # 1. Crowd heuristics
    if counts.get("person", 0) > 3:
        summary.append("🏟️")
        counts["person"] = 0
    elif counts.get("person", 0) > 1:
        summary.append("👥")
        counts["person"] = 0

    # 2. Anomaly: Loose Dog (dog with no person in scene)
    if counts.get("dog", 0) > 0 and counts.get("person", 0) == 0:
        summary.append("🐕⚠️")
        counts["dog"] = 0

    # 3. Everything else as single symbols
    for obj, count in counts.items():
        if count > 0:
            emoji = EMOJI_MAP.get(obj, f"[{obj}]")
            summary.append(f"{emoji} x{count}" if count > 1 else emoji)

    return " ".join(summary)

# FIX #1: Cache Sun object and coordinates at module level — not reconstructed every call
_lat = float(os.environ.get("LATITUDE", "42.37"))
_lon = float(os.environ.get("LONGITUDE", "-71.11"))
_sun = Sun(_lat, _lon)

def is_daytime():
    """Mathematical sun position for dynamic exposure limits."""
    now = datetime.now(timezone.utc)
    try:
        return _sun.get_sunrise_time() < now < _sun.get_sunset_time()
    except Exception:
        return 6 <= datetime.now().hour <= 18

def is_quiet_hours():
    """Check if current time is within suppressed quiet hours."""
    hour = datetime.now().hour
    if QUIET_HOURS_START <= QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    else:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END

def calculate_image_score(detected_classes):
    """Assigns a rarity score to an image based on the YOLO detections."""
    score = 0
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}
    for obj, count in counts.items():
        base = SCORE_MAP.get(obj, 1)
        score += base * (count ** 1.5)
    score += len(counts) * 5
    return int(score)

def get_temp():
    """Reads the Raspberry Pi SoC temperature in Celsius."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read()) / 1000.0
    except Exception:
        return 0.0

def configure_camera_exposure(cam):
    """Sets camera EV and frame duration limits based on day/night."""
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
            body = f"🏆 Top Activity: {best_image_data['summary']} (Score: {best_image_data['score']})\n\n" + body

        msg.set_content(body)

        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            ctype, _ = mimetypes.guess_type(best_image_data["path"])
            maintype, subtype = (ctype or "image/jpeg").split("/", 1)
            with open(best_image_data["path"], "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename="daily_highlight.jpg")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logging.info(f"📧 Daily digest sent to {notify_email}")

        with open(LOG_FILE, "w") as f:
            f.write(f"--- Rook Log Reset ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n")

    except Exception as e:
        logging.error(f"❌ Failed to send daily digest: {e}")

def send_email_alert(emoji_summary, image_path):
    """Dispatch alert via SMTP. Runs in a background thread."""
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        notify_email = os.environ.get("NOTIFY_EMAIL")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            return

        msg = EmailMessage()
        msg["Subject"] = emoji_summary
        msg["From"] = smtp_user
        msg["To"] = notify_email
        msg.set_content(emoji_summary)

        if os.path.exists(image_path):
            ctype, _ = mimetypes.guess_type(image_path)
            maintype, subtype = (ctype or "image/jpeg").split("/", 1)
            with open(image_path, "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename="alert.jpg")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logging.info(f"📧 Alert dispatched to {notify_email}")
    except Exception as e:
        logging.error(f"❌ Failed to send email alert: {e}")

def send_slack_alert(emoji_summary):
    """Dispatch real-time text alert via Slack Webhook. Runs in a background thread."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        payload = {"text": emoji_summary}
        httpx.post(webhook_url, json=payload, timeout=5.0)
        logging.info("💬 Slack alert sent!")
    except Exception as e:
        logging.error(f"❌ Failed to send Slack alert: {e}")

# FIX #8: Non-blocking dispatch — fires both alerts in parallel background threads
def dispatch_alerts_async(img_score, emojis, out_path):
    """Fire email and Slack alerts in background threads so the main loop never blocks."""
    if is_quiet_hours():
        logging.info(f"   🔕 Quiet hours active. Alert suppressed: {emojis}")
        return

    dispatched = False
    threads = []

    if img_score >= MIN_EMAIL_SCORE:
        t = threading.Thread(target=send_email_alert, args=(emojis, out_path), daemon=True)
        threads.append(t)
        dispatched = True

    if img_score >= MIN_SLACK_SCORE:
        t = threading.Thread(target=send_slack_alert, args=(emojis,), daemon=True)
        threads.append(t)
        dispatched = True

    for t in threads:
        t.start()

    if not dispatched:
        logging.info(f"   📉 Routine event (Score: {img_score}). Confined to daily digest.")

def main():
    logging.info("🚀 Initializing Rook Engine...")

    # Boot YOLO
    logging.info("🧠 Loading YOLOv11n...")
    model = YOLO("yolo11n.pt")

    # Boot Camera — native 1080p capture, MOG2 gates on 640x360 downscale, YOLO runs at 1088px
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()
    configure_camera_exposure(cam)

    # FIX #5: Tuned MOG2 — history=200 (20s window), varThreshold=40 (more sensitive)
    mog = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)

    last_alert_time = 0
    last_detected_classes = []
    flip_180 = os.environ.get("FLIP_180", "1") == "1"
    last_daytime_check = time.time()
    last_digest_date = None
    last_thermal_check = 0          # FIX #6: Tracks last temp read time
    best_daily_image = {"score": 0, "path": None, "summary": ""}

    logging.info("🛡️ Rook is armed and watching...")

    try:
        while True:
            # FIX #6: Thermal check rate-limited to every 30 seconds (not every 100ms frame)
            now_mono = time.time()
            if now_mono - last_thermal_check > THERMAL_CHECK_INTERVAL:
                if get_temp() > 80.0:
                    logging.error("🚨🔥 CRITICAL THERMAL LIMIT REACHED! Initiating Emergency Hardware Shutdown...")
                    os.system("sudo shutdown -h now")
                    break
                last_thermal_check = now_mono

            # Capture full 1080p frame
            frame = cam.capture_array()

            # Drop alpha channel (Picamera2 returns XBGR8888)
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]

            if flip_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)

            # FIX #4: Explicit BGR→RGB conversion before YOLO (COCO weights trained on RGB)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Downscale ONLY for fast MOG2 motion gate — YOLO still gets full 1080p RGB frame
            small_frame = cv2.resize(frame, (640, 360))
            fgmask = mog.apply(small_frame)
            motion_pixels = cv2.countNonZero(fgmask)

            # Daily Digest Trigger (6 PM / 18:00)
            now_hour = datetime.now().hour
            today_date = datetime.now().strftime('%Y-%m-%d')
            if now_hour == 18 and last_digest_date != today_date:
                send_daily_digest(os.environ.get("NOTIFY_EMAIL"), best_daily_image)
                last_digest_date = today_date
                best_daily_image = {"score": 0, "path": None, "summary": ""}

            # Periodically re-evaluate day/night exposure (every 10 mins)
            if now_mono - last_daytime_check > 600:
                configure_camera_exposure(cam)
                last_daytime_check = now_mono

            if motion_pixels > MOTION_THRESHOLD_PIXELS:
                # FIX #2: ALWAYS run YOLO on motion — cooldown only gates ALERTS, not inference
                results = model(frame_rgb, imgsz=1088, conf=0.25, verbose=False)
                detected_classes = [results[0].names[int(c)] for c in results[0].boxes.cls]

                if not detected_classes:
                    logging.info("   Ghost motion (no objects found). Ignored.")
                    time.sleep(0.1)
                    continue

                emojis = translate_to_emoji_summary(detected_classes)
                logging.info(f"   Identified: {emojis}")

                # Save annotated image to tmpfs
                annotated = results[0].plot()
                out_path = "/tmp/rook_alert.jpg"
                cv2.imwrite(out_path, annotated)

                # Score and track best daily image
                img_score = calculate_image_score(detected_classes)
                if img_score > best_daily_image["score"]:
                    best_path = "/tmp/rook_best_daily.jpg"
                    cv2.imwrite(best_path, annotated)
                    best_daily_image = {"score": img_score, "path": best_path, "summary": emojis}
                    logging.info(f"   🏆 New Daily High Score: {img_score}!")

                # Gate ALERTS on cooldown — inference above always runs
                now = time.time()
                if now - last_alert_time > COOLDOWN_SECONDS:
                    if sorted(detected_classes) == sorted(last_detected_classes):
                        logging.info("   Redundant scene (no change). Skipping alert.")
                    else:
                        dispatch_alerts_async(img_score, emojis, out_path)
                        last_alert_time = now

                last_detected_classes = detected_classes

            # MOG2 loop at ~10 FPS — prevents fast background absorption of slow-moving subjects
            time.sleep(0.1)

    except KeyboardInterrupt:
        logging.info("\n🛑 Shutting down Rook Engine...")
    finally:
        cam.stop()
        cam.close()

if __name__ == "__main__":
    main()
