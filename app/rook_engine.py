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
from rook_weather import RookEnrichment

# Load Environment Variables
load_dotenv(os.path.expanduser("~/rook-env/.env"))

# ── Constants & Tunables ───────────────────────────────────────────────────────
MOTION_THRESHOLD_PIXELS = 1000  # Tuned: requires meaningful total motion to wake YOLO
MOTION_BLOB_MIN_PIXELS = 120    # Min contiguous blob at 640x360 ≈ 11×11px — catches small animals, rejects leaf scatter
COOLDOWN_SECONDS = 60           # Minimum seconds between ALERTS (not between inference)
QUIET_HOURS_START = 23          # 11 PM
QUIET_HOURS_END = 6             # 6 AM
MIN_EMAIL_SCORE = 20            # Score threshold for real-time email/MMS — unusual wildlife, rare events
MIN_SLACK_SCORE = 1             # Score threshold for lightweight Slack pings
THERMAL_CHECK_INTERVAL = 30     # Seconds between SoC temp reads (not per-frame)
THERMAL_SOFT_LIMIT = 65.0       # °C: skip 2/3 frames to reduce CPU load
THERMAL_WARN_LIMIT = 72.0       # °C: skip 5/6 frames — aggressive cooldown before hard 80°C shutdown
ARCHIVE_RATE_LIMIT_SECONDS = 30 # Minimum seconds between unclassified frame saves (kills SD write storms)
DIGEST_HOUR = 3                 # 3 AM — mathematically least-active hour, minimizes missed captures
HEARTBEAT_INTERVAL = 6 * 3600  # Slack heartbeat every 6 hours (confirms system alive)
LOG_FILE = os.path.expanduser("~/rook.log")
BEAST_CAM_DIR = os.path.expanduser("~/beast_cam")  # Wildlife crop cache

# Pre-compute MOG2 dilation kernel once at module load (avoid per-frame allocation)
_MOG2_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# ── COCO Class → Emoji ────────────────────────────────────────────────────────
EMOJI_MAP = {
    "person": "🚶", "backpack": "🎒", "umbrella": "☂️", "suitcase": "🧳", "cell phone": "📱",
    "skateboard": "🛹", "sports ball": "⚽", "frisbee": "🥏", "kite": "🪁",
    "bicycle": "🚲", "car": "🚗", "motorcycle": "🏍️",
    "bus": "🚌", "truck": "🚚",
    "dog": "🐕", "cat": "🐈", "bird": "🦅",
    "bear": "🐻", "horse": "🐎", "sheep": "🐑", "cow": "🐄"
}

# ── Rarity Scores — urban yard context ───────────────────────────────────────
# Score = how unusual is this in an urban yard? Routine → low. Anomaly/incident → high.
# Barnyard classes are COCO misclassifications in urban context — silenced.
SCORE_MAP = {
    # ─ Routine (Slack-only) ───────────────────────────────────────────────────
    "person":      2,   # Pedestrian — common
    "dog":         4,   # Dog walker
    "bird":        3,   # Common yard bird (robin, crow) — silent solo
    "backpack":    3,
    "umbrella":    3,
    "cell phone":  2,
    "motorcycle":  5,   # Road traffic; could be police motorcycle
    # ─ Notable urban events ───────────────────────────────────────────────────
    "bus":         8,   # Transit; large crowd vehicle
    "truck":      12,   # Delivery/utility — OR fire truck/ambulance (same COCO class)
    "suitcase":    8,   # Someone moving
    "kite":        6,
    "frisbee":     5,
    "sports ball": 5,
    "skateboard":  4,
    "cat":         4,
    # ─ Barnyard / urban COCO noise — silenced ────────────────────────────────
    "sheep":       1,
    "cow":         1,
    "horse":       1,
    # ─ Critical ──────────────────────────────────────────────────────────────
    "bear":      100,
    # ─ Silent solo ───────────────────────────────────────────────────────────
    "car":         1,
    "bicycle":     1,
}

# ── Daily Stats Category Membership ──────────────────────────────────────────
TRAFFIC_CLASSES     = {"car", "truck", "bus", "motorcycle", "bicycle"}
PEDESTRIAN_CLASSES  = {"person"}
ANIMAL_CLASSES      = {"bird", "dog", "cat", "bear"}
DELIVERY_CLASSES    = {"truck"}
WILDLIFE_CLASSES    = ANIMAL_CLASSES

# Classes silenced when appearing solo (too routine or urban COCO misclassification)
SILENT_SOLO_CLASSES = {"car", "bicycle", "bird", "sheep", "cow", "horse"}




# ── Translation Heuristics ────────────────────────────────────────────────────
def translate_to_emoji_summary(detected_classes):
    """
    Converts raw YOLO class list to compact emoji string.
    Defaults to single symbols; composite only for anomalies (e.g. loose dog).
    """
    summary = []
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}

    # Crowd heuristics
    if counts.get("person", 0) > 3:
        summary.append("🏟️")
        counts["person"] = 0
    elif counts.get("person", 0) > 1:
        summary.append("👥")
        counts["person"] = 0

    # Anomaly: Loose Dog (dog with no person in scene)
    if counts.get("dog", 0) > 0 and counts.get("person", 0) == 0:
        summary.append("🐕⚠️")
        counts["dog"] = 0

    # Everything else as single symbols
    for obj, count in counts.items():
        if count > 0:
            emoji = EMOJI_MAP.get(obj, f"[{obj}]")
            summary.append(f"{emoji} x{count}" if count > 1 else emoji)

    return " ".join(summary)


# ── Sun / Day-Night (cached at module level) ──────────────────────────────────
_lat = float(os.environ.get("LATITUDE", "0.0"))
_lon = float(os.environ.get("LONGITUDE", "0.0"))
_sun = Sun(_lat, _lon)


def is_daytime():
    now = datetime.now(timezone.utc)
    try:
        return _sun.get_sunrise_time() < now < _sun.get_sunset_time()
    except Exception:
        return 6 <= datetime.now().hour <= 18


def is_quiet_hours():
    hour = datetime.now().hour
    if QUIET_HOURS_START <= QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


# ── Scoring ───────────────────────────────────────────────────────────────────
def calculate_image_score(detected_classes, weather_bonus: int = 0):
    score = 0
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}

    for obj, count in counts.items():
        base = SCORE_MAP.get(obj, 1)
        score += base * (count ** 1.5)

    # Diversity bonus
    score += len(counts) * 5

    # Urban event bonuses
    person_count = counts.get("person", 0)
    if person_count >= 5:
        score += 30   # Large crowd: rally, incident, street closure
    elif person_count >= 3:
        score += 10   # Small gathering

    heavy = counts.get("truck", 0) + counts.get("bus", 0)
    if heavy >= 2:
        score += 20   # Multiple heavy vehicles: fire response, utility, crash

    if counts.get("dog", 0) > 0 and counts.get("person", 0) == 0:
        score += 15   # Loose dog anomaly

    score += weather_bonus  # From enrichment: extreme weather WMO bonus

    return int(score)



# ── Thermal ───────────────────────────────────────────────────────────────────
def get_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read()) / 1000.0
    except Exception:
        return 0.0


# ── Camera Exposure ───────────────────────────────────────────────────────────
def configure_camera_exposure(cam):
    if is_daytime():
        cam.set_controls({"ExposureValue": 0.0, "FrameDurationLimits": (33333, 33333)})
        logging.info("☀️  Camera locked to Daytime Exposure")
    else:
        cam.set_controls({"ExposureValue": 1.0, "FrameDurationLimits": (33333, 100000)})
        logging.info("🌙 Camera locked to Nighttime Exposure")


# ── Beast Cam: Cache wildlife crops for batch species ID ──────────────────────
def save_beast_cam_crop(frame_rgb, boxes, classes, names, today_dir):
    """
    Saves individual cropped bounding boxes for each detected wildlife object
    to the Beast Cam directory. Zero inference cost — pure crop + save.
    """
    os.makedirs(today_dir, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S_%f")
    for i, (box, cls_idx) in enumerate(zip(boxes, classes)):
        cls_name = names[int(cls_idx)]
        if cls_name not in WILDLIFE_CLASSES:
            continue
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        # Add 10% padding around the crop
        h, w = frame_rgb.shape[:2]
        pad_x = int((x2 - x1) * 0.1)
        pad_y = int((y2 - y1) * 0.1)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        crop = cv2.cvtColor(frame_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2BGR)
        fname = os.path.join(today_dir, f"{cls_name}_{ts}_{i}.jpg")
        cv2.imwrite(fname, crop)


# ── Daily Digest ──────────────────────────────────────────────────────────────
def send_daily_digest(notify_email, best_image_data, daily_stats, beast_cam_today_dir):
    """
    Sends a structured daily digest at 6 PM with:
    - Activity summary totals (Traffic, Pedestrians, Animals, Deliveries)
    - Top detected event of the day (image attached)
    - Beast Cam section (all wildlife crops from today)
    - System health (temp, uptime)
    - Raw log as inline text
    """
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            logging.error("Missing SMTP credentials for daily digest.")
            return

        today = datetime.now().strftime('%A, %B %-d %Y')
        temp_now = get_temp()

        # ── Build structured email body ────────────────────────────────────
        lines = [
            f"🦅  ROOK DAILY DIGEST — {today}",
            "=" * 48,
            "",
            "📊  ACTIVITY SUMMARY",
            f"   🚗  Traffic:      {daily_stats['traffic']} events",
            f"   🚶  Pedestrians:  {daily_stats['pedestrians']} events",
            f"   🐾  Animals:      {daily_stats['animals']} events",
            f"   📦  Deliveries:   {daily_stats['deliveries']} events",
            f"   📋  Total events: {daily_stats['total_events']}",
            "",
        ]

        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            lines += [
                f"🏆  TOP EVENT OF THE DAY",
                f"   {best_image_data['summary']}  (Score: {best_image_data['score']})",
                "   (See attached image)",
                "",
            ]

        # Beast Cam summary
        beast_crops = []
        if os.path.isdir(beast_cam_today_dir):
            beast_crops = sorted([
                os.path.join(beast_cam_today_dir, f)
                for f in os.listdir(beast_cam_today_dir)
                if f.endswith(".jpg")
            ])

        if beast_crops:
            lines += [
                f"🐾  BEAST CAM — {len(beast_crops)} wildlife detection(s) today",
                "   (Cropped images attached below)",
                "",
            ]
        else:
            lines += ["🐾  BEAST CAM — No wildlife detected today.", ""]

        # System health
        lines += [
            "🖥️  SYSTEM HEALTH",
            f"   🌡️  Current SoC Temp: {temp_now:.1f}°C",
            f"   📅  Report generated: {datetime.now().strftime('%H:%M:%S')}",
            "",
            "─" * 48,
            "RAW LOG (today):",
            "",
        ]

        # Append raw log
        try:
            with open(LOG_FILE, "r") as f:
                lines.append(f.read())
        except Exception:
            lines.append("[Log unavailable]")

        body = "\n".join(lines)

        # ── Compose email ──────────────────────────────────────────────────
        msg = EmailMessage()
        msg["Subject"] = f"🦅 Rook Daily Digest — {datetime.now().strftime('%b %-d')}"
        msg["From"] = smtp_user
        msg["To"] = notify_email
        msg.set_content(body)

        # Attach best image
        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            with open(best_image_data["path"], "rb") as f:
                msg.add_attachment(f.read(), maintype="image", subtype="jpeg",
                                   filename="top_event.jpg")

        # Attach Beast Cam crops (max 10 to keep email size reasonable)
        for crop_path in beast_crops[:10]:
            with open(crop_path, "rb") as f:
                fname = os.path.basename(crop_path)
                msg.add_attachment(f.read(), maintype="image", subtype="jpeg", filename=fname)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logging.info(f"📧 Daily digest sent to {notify_email} ({len(beast_crops)} beast cam crops)")

        # Clear Beast Cam cache immediately after successful delivery
        # User has the images in their inbox — no need to keep them on device.
        if os.path.isdir(beast_cam_today_dir):
            import shutil
            shutil.rmtree(beast_cam_today_dir, ignore_errors=True)
            logging.info(f"🗑️  Beast Cam cache cleared: {beast_cam_today_dir}")

        # Reset log
        with open(LOG_FILE, "w") as f:
            f.write(f"--- Rook Log Reset ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n")

    except Exception as e:
        logging.error(f"❌ Failed to send daily digest: {e}")


# ── Real-Time Alert Dispatch ──────────────────────────────────────────────────
def send_email_alert(emoji_summary, image_path):
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
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        httpx.post(webhook_url, json={"text": emoji_summary}, timeout=5.0)
        logging.info("💬 Slack alert sent!")
    except Exception as e:
        logging.error(f"❌ Failed to send Slack alert: {e}")


def send_heartbeat():
    """Periodic Slack ping confirming the engine is alive. Fires every HEARTBEAT_INTERVAL seconds."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    temp = get_temp()
    uptime = open("/proc/uptime").read().split()[0]
    hours = int(float(uptime)) // 3600
    text = f"💚 Rook heartbeat — system active | {temp:.1f}°C | up {hours}h"
    try:
        httpx.post(webhook_url, json={"text": text}, timeout=5.0)
        logging.info(f"💚 Heartbeat sent: {temp:.1f}°C, up {hours}h")
    except Exception as e:
        logging.warning(f"Heartbeat failed (non-critical): {e}")


def dispatch_alerts_async(img_score, emojis, out_path, detected_classes):
    """Fire email and Slack in parallel background threads — main loop never blocks.

    Silent solo suppression: if every detected class is in SILENT_SOLO_CLASSES
    (e.g. car-only scene), skip real-time alerts entirely. Stats are unaffected.
    """
    unique = set(detected_classes)
    if unique and unique.issubset(SILENT_SOLO_CLASSES):
        logging.info(f"   🚗 Silent solo class(es) {unique}. Counted in stats, no alert.")
        return

    if is_quiet_hours():
        logging.info(f"   🔕 Quiet hours active. Alert suppressed: {emojis}")
        return

    threads = []
    if img_score >= MIN_EMAIL_SCORE:
        threads.append(threading.Thread(target=send_email_alert, args=(emojis, out_path), daemon=True))
    if img_score >= MIN_SLACK_SCORE:
        threads.append(threading.Thread(target=send_slack_alert, args=(emojis,), daemon=True))

    if threads:
        for t in threads:
            t.start()
    else:
        logging.info(f"   📉 Routine event (Score: {img_score}). Confined to daily digest.")


# ── Beast Cam Purge ───────────────────────────────────────────────────────────
def _purge_old_beast_cam(days: int = 7):
    """Delete Beast Cam date-directories older than `days` to prevent SD card fill-up."""
    import shutil
    cutoff = time.time() - days * 86400
    try:
        if not os.path.isdir(BEAST_CAM_DIR):
            return
        for entry in os.scandir(BEAST_CAM_DIR):
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
                logging.info(f"🗑️  Purged old Beast Cam dir: {entry.name}")
    except Exception as e:
        logging.warning(f"Beast Cam purge failed: {e}")


# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    logging.info("🚀 Initializing Rook Engine...")

    model = YOLO("yolo11n.pt")
    logging.info("🧠 YOLOv11n loaded.")

    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()
    configure_camera_exposure(cam)

    mog = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)

    # Enrichment: weather + local iNat species context (zero inference cost)
    enrichment = RookEnrichment(lat=_lat, lon=_lon)
    enrichment.start()
    logging.info("🌍 Enrichment service started (weather + iNat species context)")

    # ── Per-day state ──────────────────────────────────────────────────────
    def fresh_stats():
        return {"traffic": 0, "pedestrians": 0, "animals": 0, "deliveries": 0, "total_events": 0}

    daily_stats = fresh_stats()
    best_daily_image = {"score": 0, "path": None, "summary": ""}
    today_date = datetime.now().strftime('%Y-%m-%d')
    beast_cam_today_dir = os.path.join(BEAST_CAM_DIR, today_date)
    os.makedirs(beast_cam_today_dir, exist_ok=True)

    last_alert_time = 0
    last_detected_classes = []
    flip_180 = os.environ.get("FLIP_180", "1") == "1"
    last_daytime_check = time.time()
    last_thermal_check = 0
    last_digest_date = None
    last_heartbeat = time.time()  # Prevents immediate heartbeat on startup

    # Startup notification
    threading.Thread(target=send_slack_alert,
                     args=("💚 Rook engine started — system armed and watching.",),
                     daemon=True).start()

    logging.info("🛡️ Rook is armed and watching...")

    try:
        while True:
            # ── Thermal guard (every 30s, not per-frame) ───────────────────
            now_mono = time.time()
            if now_mono - last_thermal_check > THERMAL_CHECK_INTERVAL:
                if get_temp() > 80.0:
                    logging.error("🚨🔥 CRITICAL THERMAL LIMIT REACHED! Initiating Emergency Shutdown...")
                    send_slack_alert("🔴🔥 Rook THERMAL SHUTDOWN — SoC exceeded 80°C. Device halting.")
                    os.system("sudo shutdown -h now")
                    break
                last_thermal_check = now_mono

            # ── Heartbeat (every 6h) ────────────────────────────────────────
            if now_mono - last_heartbeat > HEARTBEAT_INTERVAL:
                threading.Thread(target=send_heartbeat, daemon=True).start()
                last_heartbeat = now_mono

            # ── Capture & orient frame ─────────────────────────────────────
            frame = cam.capture_array()
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]
            if flip_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)

            # ── Thermal-aware frame skipping ───────────────────────────────
            # Read temp from cache (updated every THERMAL_CHECK_INTERVAL seconds)
            current_temp = get_temp()
            frame_counter = getattr(main, '_frame_counter', 0) + 1
            main._frame_counter = frame_counter
            if current_temp >= THERMAL_WARN_LIMIT:
                # 72°C+: process 1 in 6 frames — aggressive cooldown
                if frame_counter % 6 != 0:
                    time.sleep(0.2)
                    continue
            elif current_temp >= THERMAL_SOFT_LIMIT:
                # 65°C+: process 1 in 3 frames — moderate throttle
                if frame_counter % 3 != 0:
                    time.sleep(0.15)
                    continue

            # ── Motion gate (MOG2 on 640×360 downscale) ───────────────────
            small_frame = cv2.resize(frame, (640, 360))
            fgmask = mog.apply(small_frame)
            motion_pixels = cv2.countNonZero(fgmask)

            # Two-stage gate: cheap pixel count first, expensive blob analysis only if needed.
            # Dilation merges nearby pixels so small animals form one measurable blob.
            # Wind scatter stays diffuse and fails the blob floor even after dilation.
            largest_blob = 0
            if motion_pixels > MOTION_THRESHOLD_PIXELS:
                fgmask_dilated = cv2.dilate(fgmask, _MOG2_KERNEL, iterations=1)
                _contours, _ = cv2.findContours(fgmask_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                largest_blob = max((cv2.contourArea(c) for c in _contours), default=0)

            # Frame heuristics: fog/low-light (<5ms) — only when motion qualifies
            frame_condition = RookEnrichment.analyze_frame(frame) if largest_blob > MOTION_BLOB_MIN_PIXELS else None

            # ── Date rollover & daily digest trigger ───────────────────────
            now_hour = datetime.now().hour
            new_date = datetime.now().strftime('%Y-%m-%d')

            if new_date != today_date:
                # Midnight rollover: reset all daily state
                today_date = new_date
                beast_cam_today_dir = os.path.join(BEAST_CAM_DIR, today_date)
                os.makedirs(beast_cam_today_dir, exist_ok=True)
                daily_stats = fresh_stats()
                best_daily_image = {"score": 0, "path": None, "summary": ""}

            if now_hour == DIGEST_HOUR and last_digest_date != today_date:
                # Run digest in background — SMTP + crop attachment can take 10-30s
                threading.Thread(
                    target=send_daily_digest,
                    args=(os.environ.get("NOTIFY_EMAIL"), best_daily_image,
                          daily_stats, os.path.join(BEAST_CAM_DIR, today_date)),
                    daemon=True,
                ).start()
                last_digest_date = today_date
                # Purge Beast Cam dirs older than 7 days to protect SD card
                _purge_old_beast_cam(days=7)

            # Re-evaluate exposure every 10 minutes
            if now_mono - last_daytime_check > 600:
                configure_camera_exposure(cam)
                last_daytime_check = now_mono

            # ── YOLO inference (always runs on motion) ─────────────────────
            if motion_pixels > MOTION_THRESHOLD_PIXELS and largest_blob > MOTION_BLOB_MIN_PIXELS:
                results = model(frame_rgb, imgsz=1088, conf=0.30, verbose=False)
                detected_classes = [results[0].names[int(c)] for c in results[0].boxes.cls]

                if not detected_classes:
                    # Rate-limit SD writes: max 1 unclassified save per ARCHIVE_RATE_LIMIT_SECONDS
                    # Prevents write storms (2500+ files/day) from thrashing the SD card & heating the SoC
                    now_mono_arc = time.time()
                    last_archive_save = getattr(main, '_last_archive_save', 0)
                    if now_mono_arc - last_archive_save >= ARCHIVE_RATE_LIMIT_SECONDS:
                        logging.info("   Ghost motion (unclassified). Archiving frame for training.")
                        archive_dir = os.path.expanduser("~/rook-archive/unclassified")
                        os.makedirs(archive_dir, exist_ok=True)
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        cv2.imwrite(os.path.join(archive_dir, f"unclassified_{ts}.jpg"), frame)
                        main._last_archive_save = now_mono_arc
                    else:
                        logging.debug("   Ghost motion (rate-limited, skipping save).")
                    time.sleep(0.15)
                    continue

                # ── Update daily stats ─────────────────────────────────────
                daily_stats["total_events"] += 1
                for cls in set(detected_classes):
                    if cls in TRAFFIC_CLASSES:
                        daily_stats["traffic"] += 1
                    if cls in PEDESTRIAN_CLASSES:
                        daily_stats["pedestrians"] += 1
                    if cls in ANIMAL_CLASSES:
                        daily_stats["animals"] += 1
                    if cls in DELIVERY_CLASSES:
                        daily_stats["deliveries"] += 1

                # ── Beast Cam: cache wildlife crops (async, non-blocking) ──
                wildlife_in_frame = [c for c in detected_classes if c in WILDLIFE_CLASSES]
                if wildlife_in_frame:
                    threading.Thread(
                        target=save_beast_cam_crop,
                        args=(frame_rgb, results[0].boxes, results[0].boxes.cls,
                              results[0].names, beast_cam_today_dir),
                        daemon=True,
                    ).start()

                # ── Build alert string ─────────────────────────────────────
                emojis = translate_to_emoji_summary(detected_classes)

                # Append notable weather condition
                weather_emoji = enrichment.get_weather_emoji()
                if weather_emoji and enrichment.get_weather_score_bonus() > 0:
                    emojis = f"{emojis} {weather_emoji}"

                # Append vision-detected frame condition (fog / deep night)
                if frame_condition:
                    emojis = f"{emojis} {frame_condition}"

                # Local species hint is logged, but EXCLUDED from the emoji notification
                if wildlife_in_frame:
                    hint = enrichment.get_species_hint(wildlife_in_frame[0])
                    if hint:
                        logging.info(f"   iNat species context: {hint}")

                logging.info(f"   Identified: {emojis}")

                # Save annotated alert image to tmpfs
                annotated = results[0].plot()
                out_path = "/tmp/rook_alert.jpg"
                cv2.imwrite(out_path, annotated)

                # Daily best image tracking
                img_score = calculate_image_score(detected_classes)
                if img_score > best_daily_image["score"]:
                    best_path = "/tmp/rook_best_daily.jpg"
                    cv2.imwrite(best_path, annotated)
                    best_daily_image = {"score": img_score, "path": best_path, "summary": emojis}
                    logging.info(f"   🏆 New Daily High Score: {img_score}!")

                # ── Alert gate (cooldown + redundancy) ────────────────────
                now = time.time()
                if now - last_alert_time > COOLDOWN_SECONDS:
                    if sorted(detected_classes) == sorted(last_detected_classes):
                        logging.info("   Redundant scene (no change). Skipping alert.")
                        last_alert_time = now  # Reset cooldown to avoid burst on next cycle
                    else:
                        dispatch_alerts_async(img_score, emojis, out_path, detected_classes)
                        last_alert_time = now

                last_detected_classes = detected_classes

            time.sleep(0.15)  # Base loop cadence: ~6fps max, reduces idle CPU heat

    except KeyboardInterrupt:
        logging.info("\n🛑 Shutting down Rook Engine...")
    finally:
        cam.stop()
        cam.close()


if __name__ == "__main__":
    main()
