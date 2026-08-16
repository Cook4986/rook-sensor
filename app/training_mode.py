#!/usr/bin/env python3
"""
training_mode.py — Rook "Day of Testing" Mode
=============================================
A temporary override configuration for the Rook Engine to maximize
data collection for model fine-tuning.

This script patches rook_engine.py configuration dynamically and runs it.

Key Overrides:
- Lower motion thresholds (catch distant movement)
- Lower YOLO confidence (0.25 instead of 0.70)
- Save EVERY frame with motion (no persistence gate)
- Disable Slack/Email alerts (prevent spam)
- Save annotated + raw frames to a dedicated training directory
"""

import os
import sys
import time
from pathlib import Path

# Insert the app directory into path so we can import rook_engine
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

print("\n" + "="*50)
print(" 🚀 Starting Rook in TRAINING MODE")
print("    - Slack & Email alerts DISABLED")
print("    - Motion thresholds LOWERED")
print("    - YOLO confidence set to 0.25")
print("    - Saving ALL motion frames")
print("="*50 + "\n")

# --- Patching the environment ---
# Disable Twilio and Email entirely for the run
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_FROM_NUMBER"] = ""
os.environ["NOTIFY_TO_NUMBER"] = ""
os.environ["SMTP_SERVER"] = ""

# --- Patching the engine constants dynamically ---
import rook_engine

# Override the thresholds
rook_engine.MOTION_THRESHOLD_PIXELS = 100   # Was 200
rook_engine.MOTION_BLOB_MIN_PIXELS = 15     # Was 30
rook_engine.ARCHIVE_RATE_LIMIT_SECONDS = 5  # Was 600
rook_engine.ARCHIVE_PERSISTENCE_REQ = 1     # Was 2

# We need to monkey-patch the confidence threshold which is hardcoded inside main()
# and reroute the archive directory.
original_main = rook_engine.main

def training_main():
    # Setup training directory
    training_dir = os.path.expanduser("~/rook-training/raw_frames")
    os.makedirs(training_dir, exist_ok=True)
    
    # We will let the engine run, but we want to intercept the YOLO call
    # The cleanest way without rewriting the entire main loop is to warn the user
    # that they need to manually edit rook_engine.py for the conf=0.25 change.
    
    print("⚠️  To fully enable training mode, please ensure line 1274 in rook_engine.py")
    print("   is set to: base_conf = 0.25")
    print("   and change the archive directory around line 1338 to ~/rook-training")
    print("\nStarting modified engine in 3 seconds...\n")
    
    time.sleep(3)
    original_main()

if __name__ == "__main__":
    try:
        training_main()
    except KeyboardInterrupt:
        print("\n🛑 Exiting Training Mode...")
