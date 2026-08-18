#!/usr/bin/env python3
"""
training_mode.py — Rook "Day of Testing" Mode
=============================================
A temporary override configuration for the Rook Engine to maximize
data collection for model fine-tuning.

This script sets ROOK_* environment variables *before* importing rook_engine,
then runs the unmodified engine. This works because `load_dotenv` (called at
rook_engine import time) never overrides variables already present in
os.environ — so these overrides win over whatever is in ~/rook-env/.env.

Key Overrides:
- Lower motion thresholds (catch distant movement)
- Lower YOLO confidence (0.25 instead of 0.70)
- Save nearly every frame with motion (rate limit + persistence gate dropped)
- Disable Slack/Email alerts (prevent spam)
- Archive raw frames to a dedicated training directory
"""

import os
import sys
from pathlib import Path

# Insert the app directory into path so we can import rook_engine
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

# --- Set ROOK_* overrides before rook_engine (and its load_dotenv call) runs ---
os.environ["ROOK_MOTION_THRESHOLD_PIXELS"] = "100"   # Was 200
os.environ["ROOK_MOTION_BLOB_MIN_PIXELS"] = "15"     # Was 30
os.environ["ROOK_YOLO_CONF"] = "0.25"                # Was 0.70
os.environ["ROOK_ARCHIVE_RATE_LIMIT_SECONDS"] = "5"  # Was 600 — near every event captured
os.environ["ROOK_ARCHIVE_PERSISTENCE_REQ"] = "1"     # Was 2 — no multi-pass persistence gate
os.environ["ROOK_ARCHIVE_ROOT"] = os.path.expanduser("~/rook-training")
os.environ["ROOK_ALERTS_ENABLED"] = "0"              # Suppress email/Slack/heartbeat/digest

# send_test_email() is a one-shot diagnostic gated by TEST_EMAIL, not by
# ROOK_ALERTS_ENABLED (see rook_engine.py). Force it off here so a stray
# TEST_EMAIL=1 left in ~/rook-env/.env can't fire an email during a training run.
os.environ["TEST_EMAIL"] = "0"

# --- Disable Twilio/SMS entirely for the run (unrelated to rook_engine config) ---
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_FROM_NUMBER"] = ""
os.environ["NOTIFY_TO_NUMBER"] = ""

import rook_engine  # noqa: E402  (must import after env overrides are set)


def _print_banner():
    print("\n" + "=" * 50)
    print(" 🚀 Starting Rook in TRAINING MODE")
    print(f"    - Alerts enabled:        {rook_engine.ALERTS_ENABLED}")
    print(f"    - Motion threshold:      {rook_engine.MOTION_THRESHOLD_PIXELS}px "
          f"(blob min {rook_engine.MOTION_BLOB_MIN_PIXELS}px)")
    print(f"    - YOLO confidence:       {rook_engine.YOLO_CONF}")
    print(f"    - Archive rate limit:    {rook_engine.ARCHIVE_RATE_LIMIT_SECONDS}s")
    print(f"    - Archive persistence:   {rook_engine.ARCHIVE_PERSISTENCE_REQ} pass(es)")
    print(f"    - Archive root:          {rook_engine.ARCHIVE_ROOT}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    _print_banner()
    try:
        rook_engine.main()
    except KeyboardInterrupt:
        print("\n🛑 Exiting Training Mode...")
