import os
import json
import time
import cv2
import smtplib
import threading
from email.message import EmailMessage
import mimetypes
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from picamera2 import Picamera2
from ultralytics import YOLO
from suntime import Sun
import httpx
from rook_weather import RookEnrichment

# Load Environment Variables
load_dotenv(os.path.expanduser("~/rook-env/.env"))

# ── Constants & Tunables ───────────────────────────────────────────────────────
MOTION_THRESHOLD_PIXELS = 200   # ~50-100px changed for a 30px far subject; 200 passes anything YOLO can detect
MOTION_BLOB_MIN_PIXELS = 30     # Min contiguous blob after dilation — lowered from 80 to catch distant park subjects (~5x6px blobs at 640x360)
COOLDOWN_SECONDS = 60           # Minimum seconds between ALERTS (not between inference)
QUIET_HOURS_START = 23          # 11 PM
QUIET_HOURS_END = 6             # 6 AM
MIN_EMAIL_SCORE = 30            # Score threshold for real-time email/MMS
MIN_SLACK_SCORE = 30            # Score threshold for Slack — aligned with high-relevance events only (>=30)
THERMAL_CHECK_INTERVAL = 30     # Seconds between SoC temp reads (not per-frame)
THERMAL_SOFT_LIMIT = 65.0       # °C: skip 2/3 frames to reduce CPU load
THERMAL_WARN_LIMIT = 72.0       # °C: skip 5/6 frames — aggressive cooldown before hard 80°C shutdown
ARCHIVE_RATE_LIMIT_SECONDS = 600  # Minimum seconds between unclassified frame saves — 10 min (was 5); reduces clockwork ambient captures
ARCHIVE_MIN_BLOB_PIXELS    = 800  # Cohesive blob must be ≥ this area (px²) — raised from 500 to reject marginal foliage blobs
ARCHIVE_MIN_CONCENTRATION  = 0.40 # largest_blob / motion_pixels ratio — tightened from 0.35; wind is diffuse (~0.1–0.2), real subjects ≥0.40
ARCHIVE_PERSISTENCE_REQ    = 2    # Must trigger on N consecutive qualifying YOLO passes before archiving — filters one-off wind gusts
DIGEST_HOUR = 3                 # 3 AM — mathematically least-active hour, minimizes missed captures
HEARTBEAT_INTERVAL = 6 * 3600  # Slack heartbeat every 6 hours (confirms system alive)
LOG_FILE = os.path.expanduser("~/rook.log")
BEAST_CAM_DIR = os.path.expanduser("~/beast_cam")  # Wildlife crop cache

# Pre-compute MOG2 dilation kernel once at module load (avoid per-frame allocation)
_MOG2_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

# ── Logging: rotate at 5MB, keep 3 backups — prevents SD card fill after weeks of runtime
_log_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        _log_handler,
        logging.StreamHandler()
    ]
)

# ── COCO Class → Emoji ────────────────────────────────────────────────────────
EMOJI_MAP = {
    # People & personal items
    "person": "🚶", "backpack": "🎒", "umbrella": "☂️", "suitcase": "🧳",
    "cell phone": "📱", "handbag": "👜", "tie": "👔",
    # Recreational
    "skateboard": "🛹", "sports ball": "⚽", "frisbee": "🥏", "kite": "🪁",
    "skis": "⛷️", "snowboard": "🏂", "surfboard": "🏄", "tennis racket": "🎾",
    "baseball bat": "⚾", "baseball glove": "🧤",
    # Vehicles
    "bicycle": "🚲", "car": "🚗", "motorcycle": "🏍️",
    "bus": "🚌", "truck": "🚚", "boat": "⛵", "airplane": "✈️",
    # Wildlife (relevant urban/suburban)
    "dog": "🐕", "cat": "🐈", "bird": "🦅", "bear": "🐻", "horse": "🐎",
    # Custom classes (IDs 80-85, fine-tuned model — see docs/llm_autolabel_pipeline.md).
    # Inert when the base 80-class COCO model is loaded: these names never appear
    # in detections, so all custom-class maps below are no-ops until a custom
    # model is deployed via deploy_model_to_pi.sh.
    "trash_truck": "🗑️", "ups_truck": "🟫", "fedex_truck": "🟪",
    "amazon_van": "📦", "usps_truck": "📮", "baseball_player": "🧢",
}


# Score = how notable is this for an urban street scene?
SCORE_MAP = {
    # ─ Background / silent solo ───────────────────────────────────────────────
    "car":         1,
    "bicycle":     1,
    "cell phone":  2,
    # ─ Pedestrian activity ────────────────────────────────────────────────────
    "person":      2,
    "dog":         4,
    "cat":         4,
    "backpack":    3,
    "umbrella":    5,
    "skateboard":  4,
    "motorcycle":  5,
    # ─ Recreational ───────────────────────────────────────────────────────────
    "sports ball": 5,
    "frisbee":     5,
    "kite":        8,
    "tennis racket": 5,
    "baseball bat": 8,
    "skis":        12,
    "snowboard":   12,
    "surfboard":   10,
    "suitcase":    8,
    "handbag":     4,
    "tie":         5,
    # ─ Vehicles ───────────────────────────────────────────────────────────────
    "bus":         8,
    "truck":      12,
    "boat":        10,
    "airplane":   12,
    # ─ Wildlife ───────────────────────────────────────────────────────────────
    "bird":       10,   # Airborne activity scores high, but gated by strict confidence interval
    "horse":       8,
    # ─ Critical ───────────────────────────────────────────────────────────────
    "bear":      100,
    # ─ Custom classes (fine-tuned model, IDs 80-85 — see docs/llm_autolabel_pipeline.md) ──
    "trash_truck":    15,   # Municipal: high recurrence cadence, contextual AM timing
    "ups_truck":      12,   # Delivery: UPS brown livery
    "fedex_truck":    12,   # Delivery: FedEx purple/orange or white
    "amazon_van":     12,   # Delivery: Amazon blue Sprinter/Transit Connect
    "usps_truck":     12,   # Delivery: USPS white LLV, blue eagle
    "baseball_player": 8,   # Custom: uniform + equipment
    # "baseball_game" is not a detector class — it's a congregation heuristic:
    # 3+ baseball_player detections floor the score at 50 (see calculate_image_score).
}

# ── Custom Detection Vocabulary (fine-tuned model only) ──────────────────────
# Fine-grained local classes appended to COCO as IDs 80-85 by the LLM auto-label
# pipeline (docs/llm_autolabel_pipeline.md). The class list is the contract shared
# with llm_autolabel.py / train_custom_model.py. With the stock COCO model these
# names never occur, so every reference below is a silent no-op.
CUSTOM_DELIVERY_CLASSES = {"ups_truck", "fedex_truck", "amazon_van", "usps_truck"}
CUSTOM_CLASSES = CUSTOM_DELIVERY_CLASSES | {"trash_truck", "baseball_player"}

# ── Daily Stats Category Membership ──────────────────────────────────────────
TRAFFIC_CLASSES    = {"car", "truck", "bus", "motorcycle", "bicycle",
                      "trash_truck"} | CUSTOM_DELIVERY_CLASSES

# Classes fully suppressed from detection — not present in this scene and cause misclassification noise.
# "train"          🚂  No rail infrastructure nearby — boxy dark vehicle misclassification.
# "traffic light"  🚦  Park houselight persistently misclassified as traffic light.
# "boat"           ⛵  No navigable water nearby — park fence/reflective surface misclassification (observed live).
# "fire hydrant"   🧯  Permanent street fixture — will never loiter in or out of scene.
# "stop sign"      🛑  Permanent street fixture — static infrastructure, not an event.
# "parking meter"  🅿️  Permanent street fixture — static infrastructure, not an event.
# "bench"          🪑  Permanent park fixture — static furniture, not an event.
IGNORED_CLASSES    = {"train", "traffic light", "boat",
                      "fire hydrant", "stop sign", "parking meter", "bench"}

# ── Lingerer Detection Thresholds ─────────────────────────────────────────────
# How long an object must occupy the same scene zone before a lingering alert fires.
# Designed to catch parked cars and loitering individuals without spamming.
LINGER_THRESHOLDS = {
    "car":        3600,  # 1 hour  — parked vehicle
    # "truck" intentionally omitted — trash/delivery trucks operate on a 2-5 min cycle,
    # well below any useful lingering threshold. Add to custom training plan for proper detection.
    "motorcycle": 1800,  # 30 min  — parked bike
    "bicycle":    1800,  # 30 min  — unattended bicycle
    "person":      300,  # 5 min   — loitering individual
}
LINGER_ZONE_GRID  = 4    # NxN grid cells for zone comparison (coarser = more tolerant of drift)
LINGER_COOLDOWN   = 900  # Re-alert at most every 15 min per lingering object (prevents spam)
FORCED_YOLO_INTERVAL = 300  # Seconds between forced YOLO runs bypassing MOG2 gate (catches absorbed objects)
LINGER_GRACE_FRAMES  = 3    # Tolerate N consecutive missed detections before evicting a lingerer
                            # (covers brief occlusions, confidence oscillation at 0.70, thermal frame-skip gaps)

# Per-class emoji shown when a lingering threshold fires.
# Truck detected early morning at the curb → almost certainly a trash truck.
LINGER_EMOJI = {
    "car":        "🚗🔒",   # Parked car
    # truck omitted — see LINGER_THRESHOLDS note
    "motorcycle": "🏍️🔒",  # Parked motorcycle
    "bicycle":    "🚲🔒",   # Unattended bicycle
    "person":     "🚶⏱️",   # Loitering individual / group
}
PEDESTRIAN_CLASSES = {"person", "baseball_player"}
ANIMAL_CLASSES     = {"bird", "dog", "cat", "bear", "horse"}
DELIVERY_CLASSES   = {"truck"} | CUSTOM_DELIVERY_CLASSES
WILDLIFE_CLASSES   = ANIMAL_CLASSES

# Classes silenced when appearing solo (background noise)
SILENT_SOLO_CLASSES = {"car", "bicycle", "horse"}


# ── Scene Fixture Filter ──────────────────────────────────────────────────────
class SceneFixtureFilter:
    """
    Auto-learns permanently static objects from YOLO detections.

    Problem: A bright houselight across the park, a fixed signpost, or a parked
    object that hasn't moved in days will appear in every inference pass — consuming
    alert budget and polluting stats. YOLO has no concept of "this has always been here."

    Mechanism:
    - Maintains a rolling hit-rate per (class, zone) tuple over the last N inferences.
    - Zone is derived from the bbox centroid mapped to a coarse LINGER_ZONE_GRID×LINGER_ZONE_GRID grid.
    - If a (class, zone) combo appears in ≥ FIXTURE_HIT_RATE of the last FIXTURE_WINDOW
      inferences, it is declared a FIXTURE and silently dropped from detected_classes.
    - Fixtures are logged once on promotion and re-evaluated daily.

    Zero inference cost: pure Python dict ops, negligible CPU.
    """
    FIXTURE_WINDOW   = 60    # Rolling window of inference results
    FIXTURE_HIT_RATE = 0.80  # 80% presence = fixture

    def __init__(self):
        from collections import deque
        self._history = deque(maxlen=self.FIXTURE_WINDOW)  # Each entry: set of (class, zone) present
        self._fixtures: set = set()  # Confirmed static fixtures
        self._logged: set  = set()   # Prevents repeated fixture log spam

    def _zone(self, cx_norm: float, cy_norm: float) -> tuple:
        """Map a normalized centroid (0–1) to a coarse grid cell."""
        gx = min(int(cx_norm * LINGER_ZONE_GRID), LINGER_ZONE_GRID - 1)
        gy = min(int(cy_norm * LINGER_ZONE_GRID), LINGER_ZONE_GRID - 1)
        return (gx, gy)

    def update(self, boxes, names, frame_w: int, frame_h: int):
        """Feed the latest YOLO boxes into the rolling history and recompute fixtures."""
        present = set()
        for i, cls_id in enumerate(boxes.cls):
            cls_name = names[int(cls_id)]
            if cls_name in IGNORED_CLASSES:
                continue
            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            cx = ((x1 + x2) / 2) / max(frame_w, 1)
            cy = ((y1 + y2) / 2) / max(frame_h, 1)
            present.add((cls_name, self._zone(cx, cy)))
        self._history.append(present)

        # Only compute fixture promotions after the window is populated
        if len(self._history) < self.FIXTURE_WINDOW:
            return

        # Count hits across the window
        counts = {}
        for snapshot in self._history:
            for key in snapshot:
                counts[key] = counts.get(key, 0) + 1

        new_fixtures = set()
        for key, hits in counts.items():
            if hits / self.FIXTURE_WINDOW >= self.FIXTURE_HIT_RATE:
                new_fixtures.add(key)
                if key not in self._logged:
                    cls_name, zone = key
                    logging.info(f"   📌 Fixture detected: '{cls_name}' at zone {zone} "
                                 f"({hits}/{self.FIXTURE_WINDOW} inferences). Suppressing permanently.")
                    self._logged.add(key)
        self._fixtures = new_fixtures

    def filter(self, detected_classes: list, boxes, names, frame_w: int, frame_h: int) -> list:
        """
        Remove any detected class whose (class, zone) is a confirmed fixture.
        Returns the cleaned list.
        """
        if not self._fixtures:
            return detected_classes
        cleaned = []
        for i, cls_id in enumerate(boxes.cls):
            cls_name = names[int(cls_id)]
            if cls_name not in detected_classes:
                continue
            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            cx = ((x1 + x2) / 2) / max(frame_w, 1)
            cy = ((y1 + y2) / 2) / max(frame_h, 1)
            key = (cls_name, self._zone(cx, cy))
            if key in self._fixtures:
                # Exempt lingerer-tracked classes from fixture suppression:
                # A parked car becomes a "fixture" after ~48s of consistent presence,
                # but the lingerer needs to observe it for up to 1 hour to fire an alert.
                # Without this exemption, fixtures silently vanish before reaching
                # LINGER_THRESHOLDS, causing missed parked-car and loiterer alerts.
                if cls_name in LINGER_THRESHOLDS:
                    cleaned.append(cls_name)  # Let lingerer tracker continue observing
                    logging.debug(f"   📌 Fixture (lingerer-exempt): {cls_name} @ zone {key[1]}")
                else:
                    logging.debug(f"   📌 Fixture suppressed: {cls_name} @ zone {key[1]}")
            else:
                cleaned.append(cls_name)
        # Preserve any classes not in boxes (shouldn't happen, but safety net)
        return cleaned if cleaned else [c for c in detected_classes
                                        if not any(c == k[0] for k in self._fixtures)]

    def reset(self):
        """Called at daily rollover — gives fixtures a chance to de-promote if scene changes."""
        self._history.clear()
        self._fixtures.clear()
        self._logged.clear()
        logging.info("   📌 Fixture filter reset for new day.")


# ── Lingerer Tracker ──────────────────────────────────────────────────────────
class LingererTracker:
    """
    Detects objects that have been continuously present in the same scene zone
    beyond their class-specific threshold, then fires a single delayed alert.

    Problem: MOG2 absorbs a stationary car or loitering person into the background
    model after ~30s — they stop triggering motion detection and vanish silently.
    The periodic forced-YOLO timer (FORCED_YOLO_INTERVAL) re-injects the scene
    regardless of motion so this tracker can observe still objects.

    Algorithm:
    - On each YOLO inference, map each detected (class, zone) to a first-seen timestamp.
    - If still present and elapsed ≥ LINGER_THRESHOLDS[class] → fire lingering alert (once).
    - Re-alert after LINGER_COOLDOWN if still present.
    - On disappearance, evict the entry.
    """
    def __init__(self):
        # key: (class_name, zone) → {"first_seen": float, "last_alerted": float, "miss_count": int}
        self._tracked: dict = {}

    def _zone(self, cx_norm: float, cy_norm: float) -> tuple:
        gx = min(int(cx_norm * LINGER_ZONE_GRID), LINGER_ZONE_GRID - 1)
        gy = min(int(cy_norm * LINGER_ZONE_GRID), LINGER_ZONE_GRID - 1)
        return (gx, gy)

    def update(self, detected_classes: list, boxes, names,
               frame_w: int, frame_h: int) -> list:
        """
        Called after fixture filtering. Returns a list of lingering-alert emoji strings
        to be dispatched. Empty if nothing has crossed its threshold.
        """
        now = time.time()
        current_keys = set()

        for i, cls_id in enumerate(boxes.cls):
            cls_name = names[int(cls_id)]
            if cls_name not in detected_classes:
                continue  # Was fixture-filtered out
            if cls_name not in LINGER_THRESHOLDS:
                continue  # Not a tracked class

            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            cx = ((x1 + x2) / 2) / max(frame_w, 1)
            cy = ((y1 + y2) / 2) / max(frame_h, 1)
            key = (cls_name, self._zone(cx, cy))
            current_keys.add(key)

            if key not in self._tracked:
                self._tracked[key] = {"first_seen": now, "last_alerted": 0, "miss_count": 0}
            else:
                self._tracked[key]["miss_count"] = 0  # Re-detected → reset miss counter

        # Grace period eviction: tolerate brief occlusions / confidence dips
        # before discarding accumulated dwell time. At thermal throttling (1-in-6
        # frames) + FORCED_YOLO_INTERVAL=300s, each miss can span ~5 minutes,
        # so 3 grace frames = ~15 minutes of tolerance.
        to_evict = []
        for key in self._tracked:
            if key not in current_keys:
                self._tracked[key]["miss_count"] = self._tracked[key].get("miss_count", 0) + 1
                if self._tracked[key]["miss_count"] >= LINGER_GRACE_FRAMES:
                    logging.debug(f"   ⏱️  Lingerer cleared: {key[0]} left zone {key[1]} "
                                  f"(absent {self._tracked[key]['miss_count']} frames)")
                    to_evict.append(key)
                else:
                    logging.debug(f"   ⏱️  Lingerer grace: {key[0]} @ zone {key[1]} "
                                  f"(miss {self._tracked[key]['miss_count']}/{LINGER_GRACE_FRAMES})")
        for key in to_evict:
            del self._tracked[key]

        # Check thresholds and build alerts
        alerts = []
        for key, state in self._tracked.items():
            cls_name, zone = key
            threshold = LINGER_THRESHOLDS[cls_name]
            elapsed = now - state["first_seen"]
            since_alert = now - state["last_alerted"]

            if elapsed >= threshold and since_alert >= LINGER_COOLDOWN:
                mins = int(elapsed // 60)
                # Use LINGER_EMOJI for contextual loitering symbols (e.g. 🗑️🚚 for truck at curb)
                linger_sym = LINGER_EMOJI.get(cls_name, EMOJI_MAP.get(cls_name, f"[{cls_name}]"))
                alert = f"{linger_sym} {cls_name.capitalize()} lingering {mins}min"
                alerts.append(alert)
                state["last_alerted"] = now
                logging.info(f"   ⏱️  Lingering alert: {alert}")

        return alerts


# ── Color-sensitive bird classification ───────────────────────────────────────
def classify_bird_by_color(frame_bgr, box):
    """
    Attempts songbird color ID from bounding box HSV analysis.
    Returns an emoji hint or None. Fires only when bird bbox is large enough
    to be meaningful (>400px²) — avoids misclassifying distant specks.
    """
    try:
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        if (x2 - x1) * (y2 - y1) < 400:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        total = crop.shape[0] * crop.shape[1] + 1
        # Cardinal: red hue (H 0-10 or 170-180 in OpenCV 0-180 scale)
        red = (cv2.countNonZero(cv2.inRange(hsv, (0, 80, 80), (10, 255, 255))) +
               cv2.countNonZero(cv2.inRange(hsv, (170, 80, 80), (180, 255, 255)))) / total
        # Bluebird / Blue Jay: blue hue (H 100-130)
        blue = cv2.countNonZero(cv2.inRange(hsv, (100, 60, 60), (130, 255, 255))) / total
        if red > 0.15:
            return "🔴🐦"   # Possible cardinal
        if blue > 0.15:
            return "🔵🐦"   # Possible bluebird / blue jay
    except Exception:
        pass
    return None




# ── Dog Proximity Helper ─────────────────────────────────────────────────────
def _count_unaccompanied_dogs(boxes, names, frame_h: int, frame_w: int,
                               proximity_frac: float = 0.25) -> int:
    """
    Returns number of dogs NOT within proximity_frac of any person's centroid.
    Dogs near a person are assumed on-leash and suppressed from loose-dog alerts.
    proximity_frac is relative to frame diagonal.
    """
    if boxes is None or names is None or len(boxes.cls) == 0:
        return 0
    thresh = proximity_frac * (frame_w ** 2 + frame_h ** 2) ** 0.5
    persons, dogs = [], []
    for i, cls_id in enumerate(boxes.cls):
        cls_name = names[int(cls_id)]
        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if cls_name == "person":
            persons.append((cx, cy))
        elif cls_name == "dog":
            dogs.append((cx, cy))
    if not dogs:
        return 0
    if not persons:
        return len(dogs)
    unaccompanied = 0
    for dx, dy in dogs:
        if not any(((dx - px) ** 2 + (dy - py) ** 2) ** 0.5 <= thresh for px, py in persons):
            unaccompanied += 1
    return unaccompanied


# ── Translation Heuristics ────────────────────────────────────────────────────
def translate_to_emoji_summary(detected_classes, motion_pixels=0, frame_bgr=None, boxes=None, names=None):
    """
    Converts YOLO detections to compact emoji string.
    Accepts optional motion_pixels (for runner heuristic) and frame_bgr + boxes
    (for color-sensitive bird classification).
    """
    summary = []
    counts = {c: detected_classes.count(c) for c in set(detected_classes)}
    night = not is_daytime()
    quiet = is_quiet_hours()

    # Baseball game (custom model): 3+ uniformed players = organized game.
    # Fires before the generic crowd heuristics so a game isn't reported as 🏟️.
    if counts.get("baseball_player", 0) >= 3:
        summary.append("⚾🏟️")
        counts["baseball_player"] = 0
        counts["person"] = 0   # remaining persons are spectators/umpires — implied

    # Large crowd
    if counts.get("person", 0) >= 5:
        summary.append("🏟️")   # Rally / incident
        counts["person"] = 0
    elif counts.get("person", 0) >= 2:
        summary.append("👥")
        counts["person"] = 0

    # Animal cluster (3+ = pack / flock anomaly)
    if sum(counts.get(c, 0) for c in ANIMAL_CLASSES) >= 3:
        summary.append("🐾🐾🐾")

    # Atmospheric: low-flying aircraft
    if counts.get("airplane", 0) > 0:
        summary.append("✈️⬇️")
        counts["airplane"] = 0

    # Atmospheric: kite + person = active wind event
    if counts.get("kite", 0) > 0 and counts.get("person", 0) > 0:
        summary.append("🪁🌬️")
        counts["kite"] = 0

    # Runner: solo person + large motion blob (fast-moving subject)
    if counts.get("person", 0) == 1 and motion_pixels > 4000:
        summary.append("🏃")
        counts["person"] = 0

    # Cyclist
    if counts.get("bicycle", 0) > 0 and counts.get("person", 0) > 0:
        summary.append("🚴")
        counts["bicycle"] = 0
        counts["person"] = max(0, counts["person"] - 1)

    # Moving day
    if counts.get("suitcase", 0) > 0 and counts.get("person", 0) > 0:
        summary.append("🚚📦")
        counts["suitcase"] = 0

    # Loose dog / coyote heuristic — proximity-aware:
    # Dogs within 25% of frame diagonal from a person are assumed on-leash and suppressed.
    # Only spatially isolated dogs trigger alerts. COCO has no coyote class — lone dog at
    # quiet hours is the best available proxy.
    if counts.get("dog", 0) > 0:
        fh, fw = (frame_bgr.shape[:2] if frame_bgr is not None else (1080, 1920))
        unaccompanied = _count_unaccompanied_dogs(boxes, names, fh, fw)
        if unaccompanied > 0:
            if quiet or datetime.now().hour in (5, 6, 7):
                summary.append("🐺⚠️")   # Possible coyote (solo dog, quiet hours)
            else:
                summary.append("🐕⚠️")   # Loose / unaccompanied dog
        # Accompanied dogs fall through to generic 🐕 emoji via EMOJI_MAP
        counts["dog"] = max(0, counts["dog"] - unaccompanied)

    # Raptor heuristic: bird detected without people (solo, potentially large)
    if counts.get("bird", 0) > 0 and counts.get("person", 0) == 0:
        # Try color ID on bounding box if frame data available
        bird_emoji = "🦅"   # Default: assume raptor (eagle/hawk) when solo
        if frame_bgr is not None and boxes is not None:
            for i, cls_id in enumerate(boxes.cls):
                if boxes.conf[i] > 0.35:  # Only high-confidence birds
                    hint = classify_bird_by_color(frame_bgr, boxes[i])
                    if hint:
                        bird_emoji = hint
                        break
        summary.append(bird_emoji)
        counts["bird"] = 0

    # Night walker
    if night and counts.get("person", 0) > 0:
        summary.append("🌙🚶")
        counts["person"] = 0

    # Rain event: umbrella + person
    if counts.get("umbrella", 0) > 0 and counts.get("person", 0) > 0:
        summary.append("🌂🚶")
        counts["umbrella"] = 0

    # Street play: ball/frisbee + person
    play = counts.get("sports ball", 0) + counts.get("frisbee", 0)
    if play > 0 and counts.get("person", 0) > 0:
        summary.append("🏃⚽")
        counts["sports ball"] = counts["frisbee"] = 0

    # Fallthrough: remaining classes as single symbols
    for obj, count in counts.items():
        if count > 0:
            emoji = EMOJI_MAP.get(obj, f"[{obj}]")
            summary.append(f"{emoji} x{count}" if count > 1 else emoji)

    return " ".join(summary)


# ── Sun / Day-Night (cached at module level) ──────────────────────────────────
_lat = float(os.environ.get("LATITUDE") or 0.0)
_lon = float(os.environ.get("LONGITUDE") or 0.0)
_sun = Sun(_lat, _lon)


def is_daytime():
    now = datetime.now(timezone.utc)
    try:
        sr = _sun.get_sunrise_time()
        ss = _sun.get_sunset_time()
        # FIX: suntime library bug — get_sunset_time() can return yesterday's date
        # due to UTC offset crossings. If sunset < sunrise, push it forward 1 day.
        import datetime as _dt
        if ss < sr:
            ss += _dt.timedelta(days=1)
        return sr < now < ss
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

    # Diversity bonus (reduced — was ×5, inflated routine two-class scenes past alert threshold)
    score += len(counts) * 3

    # Congregation bonus: 3+ active objects in scene = multi-subject event
    total_objects = sum(counts.values())
    if total_objects >= 5:
        score += 25   # Dense congregation (rally, incident, parade)
        score = max(score, 30)
    elif total_objects >= 3:
        score += 15   # Multi-subject scene (group + pets, mixed traffic, etc.)
        score = max(score, 30)

    # Urban event bonuses
    person_count = counts.get("person", 0)
    if person_count >= 5:
        score += 30   # Large crowd: rally, incident, street closure
        score = max(score, 30)
    elif person_count >= 3:
        score += 10   # Small gathering
        score = max(score, 30)

    heavy = (counts.get("truck", 0) + counts.get("bus", 0)
             + counts.get("trash_truck", 0)
             + sum(counts.get(c, 0) for c in CUSTOM_DELIVERY_CLASSES))
    if heavy >= 1:
        score += 20   # Multiple heavy vehicles: fire response, utility, crash
        score = max(score, 30)

    # Baseball game congregation (custom model): 3+ uniformed players = organized
    # game, the "rare/critical" tier per docs/emoji_vocabulary.md alert scoring.
    if counts.get("baseball_player", 0) >= 3:
        score += 20
        score = max(score, 50)

    # Unaccompanied animals
    for animal in ["dog", "cat", "bear", "horse"]:
        if counts.get(animal, 0) > 0 and counts.get("person", 0) == 0:
            score += 15   # Loose animal anomaly
            score = max(score, 30)

    # Airborne phenomena
    if counts.get("bird", 0) > 0 or counts.get("airplane", 0) > 0 or counts.get("kite", 0) > 0:
        score = max(score, 30)
        
    # Sports
    if any(counts.get(s, 0) > 0 for s in ["sports ball", "frisbee", "tennis racket", "baseball bat", "skis", "snowboard", "surfboard"]):
        score = max(score, 30)

    # Quiet hours bonus: any person detected 11PM–6AM is inherently more notable
    if is_quiet_hours() and counts.get("person", 0) > 0:
        score += 20
        score = max(score, 30)

    score += weather_bonus  # From enrichment: extreme weather WMO bonus
    if weather_bonus > 0:
        score = max(score, 30)

    return int(score)



# ── Thermal ───────────────────────────────────────────────────────────────────
def get_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read()) / 1000.0
    except (FileNotFoundError, ValueError, OSError):
        return 0.0


# ── Camera Exposure — fires Slack on day/night transition (sunrise/sunset) ──
_last_daytime_state = None  # tracks previous state to detect transitions

def configure_camera_exposure(cam):
    global _last_daytime_state
    day = is_daytime()
    if day:
        cam.set_controls({"ExposureValue": 0.0, "FrameDurationLimits": (33333, 33333)})
        logging.info("☀️  Camera locked to Daytime Exposure")
    else:
        cam.set_controls({"ExposureValue": 1.0, "FrameDurationLimits": (33333, 100000)})
        logging.info("🌙 Camera locked to Nighttime Exposure")
    # Fire Slack only on actual transition (not every 10-min poll)
    if _last_daytime_state is not None and day != _last_daytime_state:
        msg = "🌅 Sunrise — Rook switching to daytime mode." if day else "🌆 Sunset — Rook switching to nighttime mode."
        threading.Thread(target=send_slack_alert, args=(msg,), daemon=True).start()
        threading.Thread(target=send_email_alert, args=(msg, ""), daemon=True).start()
    _last_daytime_state = day


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


# ── Persistent Stats Database ─────────────────────────────────────────────────
_STATS_DB_PATH = os.path.expanduser("~/rook-stats.json")


def load_stats_db() -> dict:
    """Load cumulative stats from disk. Returns a fresh schema if file is missing."""
    try:
        with open(_STATS_DB_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "start_date": datetime.now().strftime("%Y-%m-%d"),
            "all_time": {"traffic": 0, "pedestrians": 0, "animals": 0,
                         "deliveries": 0, "total_events": 0,
                         "best_score": 0, "best_summary": ""},
            "daily_history": [],
        }


def save_stats_db(db: dict):
    """Persist stats atomically via write-then-rename."""
    import shutil
    tmp = _STATS_DB_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(db, f, indent=2)
        shutil.move(tmp, _STATS_DB_PATH)
    except Exception as e:
        logging.warning(f"Stats DB save failed: {e}")


def append_day_to_stats_db(day_stats: dict, day_label: str,
                            best_score: int, best_summary: str):
    """Append yesterday's totals and update all-time records. Called at midnight rollover."""
    db = load_stats_db()
    try:
        import datetime as dt
        iso_date = dt.datetime.strptime(day_label, "%A, %B %d %Y").strftime("%Y-%m-%d")
    except Exception:
        iso_date = ""
        
    entry = {
        "date": day_label,
        "date_iso": iso_date,
        "traffic":      day_stats.get("traffic", 0),
        "pedestrians":  day_stats.get("pedestrians", 0),
        "animals":      day_stats.get("animals", 0),
        "deliveries":   day_stats.get("deliveries", 0),
        "total_events": day_stats.get("total_events", 0),
        "best_score":   best_score,
        "best_summary": best_summary,
    }
    db["daily_history"].append(entry)
    for key in ("traffic", "pedestrians", "animals", "deliveries", "total_events"):
        db["all_time"][key] = db["all_time"].get(key, 0) + day_stats.get(key, 0)
    if best_score > db["all_time"].get("best_score", 0):
        db["all_time"]["best_score"] = best_score
        db["all_time"]["best_summary"] = best_summary
    save_stats_db(db)
    logging.info(f"📊 Stats DB updated — {entry['total_events']} events on {day_label}")


# ── Daily Digest ──────────────────────────────────────────────────────────────
def send_daily_digest(notify_email, best_image_data, daily_stats, beast_cam_today_dir,
                      report_date_label: str = "", emoji_log: list = None):
    """
    Sends overnight digest at DIGEST_HOUR (3 AM) covering the *previous* calendar day.
    Includes yesterday's counts, cumulative stats (week/month/all-time from rook-stats.json),
    top event image, and Beast Cam wildlife crops. No raw timeline.
    """
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            logging.error("Missing SMTP credentials for daily digest.")
            return

        label    = report_date_label or datetime.now().strftime('%A, %B %-d %Y')
        temp_now = get_temp()

        # ── Cumulative stats from persistent DB ────────────────────────────
        db       = load_stats_db()
        history  = db.get("daily_history", [])
        all_time = db.get("all_time", {})
        start_dt = db.get("start_date", label)

        from datetime import timedelta as _td
        today_dt = datetime.now()

        def _window(days: int) -> dict:
            cutoff = (today_dt - _td(days=days)).strftime("%Y-%m-%d")
            rows = []
            for r in history:
                iso = r.get("date_iso")
                if not iso:
                    try:
                        import datetime as dt
                        iso = dt.datetime.strptime(r.get("date", ""), "%A, %B %d %Y").strftime("%Y-%m-%d")
                    except Exception:
                        iso = "1970-01-01"
                if iso >= cutoff:
                    rows.append(r)
            t = {"traffic": 0, "pedestrians": 0, "animals": 0, "deliveries": 0, "total_events": 0}
            for r in rows:
                for k in t:
                    t[k] += r.get(k, 0)
            return t

        week  = _window(7)
        month = _window(30)

        def _row(s: dict) -> str:
            return (f"   🚗 {s['traffic']:>5}  🚶 {s['pedestrians']:>5}  "
                    f"🐾 {s['animals']:>5}  📦 {s['deliveries']:>5}  "
                    f"📋 Total: {s['total_events']}")

        # ── Build email body ───────────────────────────────────────────────
        lines = [
            f"🦅  ROOK DAILY DIGEST — {label}",
            "=" * 48,
            "",
            "📊  YESTERDAY",
            f"   🚗  Traffic:      {daily_stats['traffic']} events",
            f"   🚶  Pedestrians:  {daily_stats['pedestrians']} events",
            f"   🐾  Animals:      {daily_stats['animals']} events",
            f"   📦  Deliveries:   {daily_stats['deliveries']} events",
            f"   📋  Total events: {daily_stats['total_events']}",
            "",
            "📈  CUMULATIVE STATS",
            "   ─ This Week ─────────────────────────────────────",
            _row(week),
            "   ─ This Month ──────────────────────────────────",
            _row(month),
            f"   ─ All Time (since {start_dt}) ───────────────",
            _row(all_time),
            f"   🏆 Best score: {all_time.get('best_score', 0)} — {all_time.get('best_summary', 'N/A')}",
            "",
        ]

        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            lines += [
                "🏆  TOP EVENT — Yesterday",
                f"   {best_image_data['summary']}  (Score: {best_image_data['score']})",
                "   (See attached image)",
                "",
            ]

        beast_crops = []
        if os.path.isdir(beast_cam_today_dir):
            beast_crops = sorted([
                os.path.join(beast_cam_today_dir, f)
                for f in os.listdir(beast_cam_today_dir)
                if f.endswith(".jpg")
            ])
        if beast_crops:
            lines += [f"🐾  BEAST CAM — {len(beast_crops)} wildlife detection(s)",
                      "   (Cropped images attached below)", ""]
        else:
            lines += ["🐾  BEAST CAM — No wildlife detected.", ""]

        lines += [
            "🖥️  SYSTEM HEALTH",
            f"   🌡️  SoC Temp: {temp_now:.1f}°C",
            f"   📅  Report:   {datetime.now().strftime('%H:%M:%S')}",
            "",
            "─" * 48,
            "Rook is watching. Check ~/rook.log on device for raw event log.",
        ]

        body = "\n".join(lines)

        # ── Compose & send ─────────────────────────────────────────────────
        msg = EmailMessage()
        msg["Subject"] = f"🦅 Rook Digest — {label}"
        msg["From"]    = smtp_user
        msg["To"]      = notify_email
        msg.set_content(body)

        if best_image_data["path"] and os.path.exists(best_image_data["path"]):
            with open(best_image_data["path"], "rb") as f:
                msg.add_attachment(f.read(), maintype="image", subtype="jpeg",
                                   filename="top_event.jpg")

        for crop_path in beast_crops[:10]:
            with open(crop_path, "rb") as f:
                msg.add_attachment(f.read(), maintype="image", subtype="jpeg",
                                   filename=os.path.basename(crop_path))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logging.info(f"📧 Daily digest sent to {notify_email} ({len(beast_crops)} beast cam crops)")

        if os.path.isdir(beast_cam_today_dir):
            import shutil
            shutil.rmtree(beast_cam_today_dir, ignore_errors=True)
            logging.info(f"🗑️  Beast Cam cache cleared: {beast_cam_today_dir}")

        try:
            for h in logging.getLogger().handlers:
                if hasattr(h, 'stream') and hasattr(h.stream, 'truncate'):
                    h.stream.seek(0)
                    h.stream.truncate()
                    h.stream.write(f"--- Rook Log Reset ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
                    h.stream.flush()
                    break
        except Exception:
            pass

    except Exception as e:
        logging.error(f"❌ Failed to send daily digest: {e}")


# ── Test Email ────────────────────────────────────────────────────────────────
def send_test_email(cam):
    """
    Captures a live frame and emails it immediately as a diagnostic test.
    Called once at startup when TEST_EMAIL env var is set to '1'.
    """
    try:
        smtp_server = os.environ.get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        notify_email = os.environ.get("NOTIFY_EMAIL")

        if not all([smtp_server, smtp_user, smtp_pass, notify_email]):
            logging.warning("⚠️  Test email skipped — missing SMTP credentials.")
            return

        logging.info("📸 Capturing test frame for email diagnostic...")
        frame = cam.capture_array()
        if frame.shape[2] == 4:
            frame = frame[:, :, :3]
        flip_180 = os.environ.get("FLIP_180", "1") == "1"
        if flip_180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)

        test_path = "/tmp/rook_test.jpg"
        cv2.imwrite(test_path, frame)

        temp_now = get_temp()
        uptime_raw = open("/proc/uptime").read().split()[0]
        hours = int(float(uptime_raw)) // 3600
        body = (
            f"🦅 Rook Test Image\n"
            f"Captured: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"SoC Temp: {temp_now:.1f}°C  |  Uptime: {hours}h\n"
            f"Frame size: {frame.shape[1]}×{frame.shape[0]}\n"
        )

        msg = EmailMessage()
        msg["Subject"] = f"🦅 Rook Test — {datetime.now().strftime('%b %-d %H:%M')}"
        msg["From"] = smtp_user
        msg["To"] = notify_email
        msg.set_content(body)

        with open(test_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="image", subtype="jpeg", filename="rook_test.jpg")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logging.info(f"📧 Test image sent to {notify_email}")

    except Exception as e:
        logging.error(f"❌ Failed to send test email: {e}")


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
        # Body is distinct from subject to prevent Gmail from showing the emoji string twice
        # in the message list preview (subject + body-snippet concatenated).
        alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg.set_content(f"Rook alert — {alert_time}\n{emoji_summary}\n")

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
        # During quiet hours: suppress EMAIL (keep the bedroom silent), but allow Slack
        # so notable overnight events (wildlife, incidents) still appear in Slack history.
        if img_score >= MIN_SLACK_SCORE:
            threading.Thread(target=send_slack_alert,
                             args=(f"🌙 {emojis}",), daemon=True).start()
        logging.info(f"   🔕 Quiet hours — email suppressed, Slack: {'sent' if img_score >= MIN_SLACK_SCORE else 'below threshold'} ({emojis})")
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

    # Model loading priority: YOLO26n NCNN → YOLO11n NCNN → YOLO26n.pt → yolo11n.pt
    # YOLO26n: 43% faster CPU inference, NMS-free end-to-end, STAL small-target detection.
    # Falls back gracefully so the engine runs with whatever model is on-device.
    _yolo26_ncnn = os.path.expanduser("~/yolo26n_1088_ncnn_model")
    _yolo11_ncnn = os.path.expanduser("~/yolo11n_1088_ncnn_model")
    _model_label = None
    if os.path.isdir(_yolo26_ncnn):
        model = YOLO(_yolo26_ncnn, task="detect")
        _model_label = "YOLO26n (NCNN)"
    elif os.path.isdir(_yolo11_ncnn):
        model = YOLO(_yolo11_ncnn, task="detect")
        _model_label = "YOLOv11n (NCNN)"
    else:
        # PyTorch fallback — try yolo26n first, then yolo11n
        try:
            model = YOLO("yolo26n.pt")
            _model_label = "YOLO26n (PyTorch)"
        except Exception:
            model = YOLO("yolo11n.pt")
            _model_label = "YOLOv11n (PyTorch)"
        logging.warning(f"⚠️  NCNN model not found, falling back to {_model_label}.")
    # Pin NCNN to 3 of Pi 5's 4 cores — leaves 1 for OS/camera ISP, reduces contention
    if "NCNN" in (_model_label or ""):
        try:
            import ncnn
            ncnn.set_num_threads(3)
            _model_label += ", 3 threads"
        except Exception:
            pass
    # Detect whether the loaded model carries the custom local vocabulary
    # (fine-tuned via the LLM auto-label pipeline) or is the stock 80-class COCO
    # model. Logged for diagnostics and surfaced to the future v2 dashboard.
    try:
        _model_classes = set(model.names.values())
    except Exception:
        _model_classes = set()
    _custom_active = sorted(CUSTOM_CLASSES & _model_classes)
    if _custom_active:
        _model_label += f" +custom[{','.join(_custom_active)}]"
    logging.info(f"🧠 {_model_label} loaded.")

    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()
    configure_camera_exposure(cam)

    mog = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)

    # Enrichment: weather + local iNat species context (zero inference cost)
    enrichment = RookEnrichment(lat=_lat, lon=_lon)
    enrichment.start()
    logging.info("🌍 Enrichment service started (weather + iNat species context)")

    # Scene intelligence trackers
    fixture_filter  = SceneFixtureFilter()   # Auto-suppresses permanently static YOLO detections
    lingerer        = LingererTracker()      # Fires delayed alerts for parked cars / loitering people

    # ── Per-day state ──────────────────────────────────────────────────────
    def fresh_stats():
        return {"traffic": 0, "pedestrians": 0, "animals": 0, "deliveries": 0, "total_events": 0}

    daily_stats = fresh_stats()
    best_daily_image = {"score": 0, "path": None, "summary": ""}
    today_date = datetime.now().strftime('%Y-%m-%d')
    beast_cam_today_dir = os.path.join(BEAST_CAM_DIR, today_date)

    # Emoji activity log: list of ("HH:MM", emoji_string) tuples, one per dispatched alert.
    # Accumulates all day, snapshotted at midnight for the digest. This IS the 24h timeline.
    daily_emoji_log: list = []

    # Snapshot of previous day's stats — digest sent at 3 AM covers yesterday, not today.
    # These are populated at midnight rollover and consumed by the digest at DIGEST_HOUR.
    prev_day_stats = fresh_stats()
    prev_day_best_image = {"score": 0, "path": None, "summary": ""}
    prev_day_emoji_log: list = []  # 24h emoji timeline for the digest
    prev_day_label = ""       # Human-readable date string for the digest subject
    prev_day_beast_dir = ""   # Beast Cam dir for the previous day
    os.makedirs(beast_cam_today_dir, exist_ok=True)

    last_alert_time = 0
    last_detected_classes = []
    flip_180 = os.environ.get("FLIP_180", "1") == "1"
    last_daytime_check = time.time()
    last_thermal_check = 0
    last_digest_date = None
    last_heartbeat = time.time()  # Prevents immediate heartbeat on startup
    last_forced_yolo = 0          # Timestamp of last periodic forced-YOLO run
    warmup_frame_count = 0        # MOG2 warmup: skip first 30 frames (no background model yet)
    archive_persistence_count = 0 # Consecutive qualifying unclassified motion events (must reach ARCHIVE_PERSISTENCE_REQ to save)

    # Startup notification — rate-limited to prevent spam during rapid restarts/power cycles.
    # Only fires if the engine has been down for > 5 minutes (i.e., a real restart, not a crash loop).
    _startup_flag = "/tmp/rook_last_startup"
    _min_startup_interval = 300  # 5 minutes
    _now = time.time()
    _last_startup = 0
    try:
        with open(_startup_flag, "r") as _f:
            _last_startup = float(_f.read().strip())
    except Exception:
        pass
    if _now - _last_startup > _min_startup_interval:
        threading.Thread(target=send_slack_alert,
                         args=("💚 Rook engine started — system armed and watching.",),
                         daemon=True).start()
    try:
        with open(_startup_flag, "w") as _f:
            _f.write(str(_now))
    except Exception:
        pass

    logging.info("🛡️ Rook is armed and watching...")

    # One-shot test email: set TEST_EMAIL=1 in .env to receive a live frame on next startup.
    # Useful for verifying SMTP credentials, camera angle, and flip orientation.
    # The env var is checked once here — remove it from .env after use.
    if os.environ.get("TEST_EMAIL", "0") == "1":
        threading.Thread(target=send_test_email, args=(cam,), daemon=True).start()

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

            # ── MOG2 warmup guard ──────────────────────────────────────────
            # First 30 frames: MOG2 has no background model, everything looks like motion.
            # Skip YOLO entirely during this phase to avoid noisy startup burst detections.
            warmup_frame_count += 1
            if warmup_frame_count <= 30:
                mog.apply(small_frame)  # Feed frames to build background, but don't act on them
                time.sleep(0.15)
                continue

            # Frame heuristics: fog/low-light (<5ms) — only when motion qualifies
            frame_condition = RookEnrichment.analyze_frame(frame) if largest_blob > MOTION_BLOB_MIN_PIXELS else None

            # ── Date rollover & daily digest trigger ───────────────────────
            now_hour = datetime.now().hour
            new_date = datetime.now().strftime('%Y-%m-%d')

            if new_date != today_date:
                # Midnight rollover: snapshot the completed day's data for the upcoming digest,
                # then reset all daily state for the new calendar day.
                from datetime import timedelta as _td
                _prev_dt = datetime.now() - _td(days=1)
                prev_day_label = _prev_dt.strftime('%A, %B %-d %Y')
                prev_day_stats = daily_stats.copy()
                prev_day_best_image = best_daily_image.copy()
                prev_day_emoji_log = list(daily_emoji_log)  # Full 24h emoji timeline
                prev_day_beast_dir = beast_cam_today_dir  # points to the completed day's crops
                # Persist stats to disk for cumulative digest reporting
                append_day_to_stats_db(
                    daily_stats, prev_day_label,
                    best_daily_image["score"], best_daily_image["summary"])

                today_date = new_date
                beast_cam_today_dir = os.path.join(BEAST_CAM_DIR, today_date)
                os.makedirs(beast_cam_today_dir, exist_ok=True)
                daily_stats = fresh_stats()
                daily_emoji_log = []  # Reset for new day
                best_daily_image = {"score": 0, "path": None, "summary": ""}
                fixture_filter.reset()  # Re-learn fixtures daily — allows scene changes to propagate

            if now_hour == DIGEST_HOUR and last_digest_date != today_date:
                # Digest covers the *previous* calendar day (midnight→midnight), not the
                # sparse 12am–3am window of the current day.
                threading.Thread(
                    target=send_daily_digest,
                    args=(os.environ.get("NOTIFY_EMAIL"), prev_day_best_image,
                          prev_day_stats, prev_day_beast_dir, prev_day_label,
                          prev_day_emoji_log),
                    daemon=True,
                ).start()
                last_digest_date = today_date
                # Purge Beast Cam dirs older than 7 days to protect SD card
                _purge_old_beast_cam(days=7)

            # Re-evaluate exposure every 10 minutes
            if now_mono - last_daytime_check > 600:
                configure_camera_exposure(cam)
                last_daytime_check = now_mono

            # ── YOLO inference gate ────────────────────────────────────────
            # Fires on real motion (MOG2), OR on the periodic forced-YOLO timer
            # which bypasses MOG2 to let LingererTracker observe absorbed-static objects.
            forced_yolo = (now_mono - last_forced_yolo > FORCED_YOLO_INTERVAL
                           and warmup_frame_count > 30)
            run_yolo = ((motion_pixels > MOTION_THRESHOLD_PIXELS and largest_blob > MOTION_BLOB_MIN_PIXELS)
                        or forced_yolo)

            if run_yolo:
                if forced_yolo:
                    last_forced_yolo = now_mono
                    logging.debug("   🔎 Forced YOLO scan (lingerer check).")
                # Confidence threshold: 0.70 across all hours to minimize false positives.
                # Previous adaptive scheme (0.30 quiet / 0.45 day) generated excessive noise.
                base_conf = 0.70
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # Run YOLO at 0.70 baseline — only high-confidence detections pass through
                results = model(frame_rgb, imgsz=1088, conf=base_conf, verbose=False)

                # Dynamic Confidence Interval: Airborne objects require higher certainty
                AIRBORNE_CLASSES = {"bird", "airplane", "kite"}
                AIRBORNE_CONF_REQ = 0.75

                detected_classes = []
                for i, cls_id in enumerate(results[0].boxes.cls):
                    cls_name = results[0].names[int(cls_id)]
                    conf = float(results[0].boxes.conf[i])
                    if cls_name in IGNORED_CLASSES:
                        continue  # Suppressed class — not present in this scene
                    if cls_name in AIRBORNE_CLASSES and conf < AIRBORNE_CONF_REQ:
                        continue  # Skip low-confidence airborne objects (distant noise)
                    detected_classes.append(cls_name)

                # Feed fixture filter (learns static objects, then silently drops them)
                frame_h, frame_w = frame.shape[:2]
                fixture_filter.update(results[0].boxes, results[0].names, frame_w, frame_h)
                detected_classes = fixture_filter.filter(
                    detected_classes, results[0].boxes, results[0].names, frame_w, frame_h
                )

                # Update lingerer tracker — returns any threshold-crossing alerts
                linger_alerts = lingerer.update(
                    detected_classes, results[0].boxes, results[0].names, frame_w, frame_h
                )

                if not detected_classes:
                    # ── Unclassified motion: multi-frame persistence gate ─────────────
                    # Problem: the old single-frame gate saved ~1,850 frames/day of ambient
                    # motion (wind, shadows, light changes) — mostly useless for training.
                    # Fix: require ARCHIVE_PERSISTENCE_REQ consecutive qualifying YOLO passes
                    # before saving. Real subjects persist across frames; wind gusts don't.
                    now_mono_arc = time.time()
                    last_archive_save = getattr(main, '_last_archive_save', 0)
                    concentration = largest_blob / max(motion_pixels, 1)
                    # Spatial heuristics: blob size, concentration, visual interest
                    qualifies = (largest_blob >= ARCHIVE_MIN_BLOB_PIXELS
                                 and concentration >= ARCHIVE_MIN_CONCENTRATION)
                    if qualifies:
                        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                        visual_interest = cv2.Laplacian(gray, cv2.CV_64F).var()
                        qualifies = visual_interest >= 250
                    if qualifies:
                        archive_persistence_count += 1
                        logging.debug(f"   Unclassified motion qualifies (blob={largest_blob:.0f}px², "
                                      f"conc={concentration:.2f}, persist={archive_persistence_count}/"
                                      f"{ARCHIVE_PERSISTENCE_REQ}).")
                    else:
                        # Failed spatial gates — reset persistence (not a real subject)
                        if archive_persistence_count > 0:
                            logging.debug(f"   Persistence reset (spatial gates failed after {archive_persistence_count} hits).")
                        archive_persistence_count = 0

                    # Only archive when persistence threshold met AND rate limiter allows
                    if (archive_persistence_count >= ARCHIVE_PERSISTENCE_REQ
                            and now_mono_arc - last_archive_save >= ARCHIVE_RATE_LIMIT_SECONDS):
                        logging.info(f"   📸 Persistent unclassified motion archived "
                                     f"(blob={largest_blob:.0f}px², conc={concentration:.2f}, "
                                     f"persist={archive_persistence_count}).")
                        archive_dir = os.path.expanduser("~/rook-archive/unclassified")
                        os.makedirs(archive_dir, exist_ok=True)
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        small_save = cv2.resize(frame, (640, 360))
                        cv2.imwrite(os.path.join(archive_dir, f"unclassified_{ts}.jpg"), small_save)
                        main._last_archive_save = now_mono_arc
                        archive_persistence_count = 0  # Reset after save
                    elif now_mono_arc - last_archive_save < ARCHIVE_RATE_LIMIT_SECONDS:
                        logging.debug("   Unclassified motion (rate-limited, skipping save).")
                    time.sleep(0.15)
                    continue

                # ── Classified detection: reset archive persistence counter ────
                # A real YOLO detection means the scene has a classifiable subject.
                # Any preceding unclassified persistence streak was likely approach
                # frames of this same subject — not worth archiving separately.
                archive_persistence_count = 0

                # ── Update daily stats (Event-based) ───────────────────────
                # Count an event when a class newly appears in the scene to accurately
                # track all non-notification events without inflating per-frame counts.
                new_classes = set(detected_classes) - set(last_detected_classes)
                if new_classes:
                    daily_stats["total_events"] += 1
                    for cls in new_classes:
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
                emojis = translate_to_emoji_summary(
                    detected_classes,
                    motion_pixels=motion_pixels,
                    frame_bgr=frame,
                    boxes=results[0].boxes,
                    names=results[0].names,
                )

                # Append notable weather condition
                weather_bonus = enrichment.get_weather_score_bonus()
                weather_emoji = enrichment.get_weather_emoji()
                if weather_emoji and weather_bonus > 0:
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

                for linger_msg in linger_alerts:
                    threading.Thread(target=send_slack_alert,
                                     args=(f"⏱️ {linger_msg}",), daemon=True).start()
                    threading.Thread(target=send_email_alert,
                                     args=(f"⏱️ {linger_msg}", out_path), daemon=True).start()
                    # Log lingerer events into the daily emoji stack
                    daily_emoji_log.append((datetime.now().strftime("%H:%M"), linger_msg))

                # Daily best image tracking
                img_score = calculate_image_score(detected_classes, weather_bonus=weather_bonus)
                if img_score > best_daily_image["score"]:
                    best_path = "/tmp/rook_best_daily.jpg"
                    cv2.imwrite(best_path, annotated)
                    best_daily_image = {"score": img_score, "path": best_path, "summary": emojis}
                    logging.info(f"   🏆 New Daily High Score: {img_score}!")

                # ── Alert gate (cooldown + redundancy) ────────────────────
                now = time.time()
                # Score-adaptive cooldown: high-priority events (bear, truck) break through quickly.
                # Curve: score ≥ 60 → 10s cooldown; score=8 → 52s; score=0 → full 60s
                effective_cooldown = max(10, COOLDOWN_SECONDS - img_score)
                if now - last_alert_time > effective_cooldown:
                    if sorted(detected_classes) == sorted(last_detected_classes):
                        logging.info("   Redundant scene (no change). Skipping alert.")
                        last_alert_time = now  # Reset cooldown to avoid burst on next cycle
                    else:
                        dispatch_alerts_async(img_score, emojis, out_path, detected_classes)
                        last_alert_time = now
                        # Log every dispatched alert into the 24h emoji timeline
                        daily_emoji_log.append((datetime.now().strftime("%H:%M"), emojis))

                last_detected_classes = detected_classes

            time.sleep(0.15)  # Base loop cadence: ~6fps max, reduces idle CPU heat

    except KeyboardInterrupt:
        logging.info("\n🛑 Shutting down Rook Engine...")
    finally:
        cam.stop()
        cam.close()


if __name__ == "__main__":
    main()
