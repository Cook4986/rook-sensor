#!/usr/bin/env python3
"""
reclassify_archive.py — Mac-side offline inference pass

Runs YOLOv11l (or larger) with Apple MPS acceleration on unclassified
frames synced from the Pi. Finds objects the Pi's nano model missed
(especially small/distant park subjects), moves interesting frames to
archive/reclassified/ with annotated images, and sends a Slack digest.

Usage:
    python3 reclassify_archive.py              # process all pending frames
    python3 reclassify_archive.py --model x   # use yolo26x.pt (maximum quality)
    python3 reclassify_archive.py --dry-run   # report without moving files

Run automatically: add to launchd alongside sync, or run manually after sync.
"""

import os
import sys
import argparse
import json
import shutil
import httpx
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv(Path.home() / ".env")

ARCHIVE_DIR      = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"
UNCLASSIFIED_DIR = ARCHIVE_DIR / "unclassified"
RECLASSIFIED_DIR = ARCHIVE_DIR / "reclassified"   # frames with new detections (annotated)
PROCESSED_DIR    = ARCHIVE_DIR / "processed"       # frames checked, nothing found — kept for audit

# Confidence threshold — lower than Pi (0.30) to catch faint distant detections
CONF_THRESHOLD = 0.20

# Classes not worth alerting on even if found (routine background)
SILENT_CLASSES = {"car", "bicycle"}

# Score map (matches Pi engine)
SCORE_MAP = {
    "person": 2, "dog": 4, "cat": 4, "bird": 3, "bear": 100, "horse": 8,
    "bicycle": 1, "car": 1, "motorcycle": 5, "bus": 8, "truck": 12,
    "airplane": 12, "boat": 10, "train": 6, "kite": 8,
    "sports ball": 5, "frisbee": 5, "skateboard": 4,
    "umbrella": 5, "backpack": 3, "suitcase": 8,
}


def score(classes):
    return sum(SCORE_MAP.get(c, 1) for c in classes)


def send_slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        try:
            httpx.post(url, json={"text": text}, timeout=5)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Rook offline re-inference pass")
    parser.add_argument("--model", default="l", choices=["n", "s", "m", "l", "x"],
                        help="YOLO26 model size (default: l)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print findings without moving files or alerting")
    parser.add_argument("--slack", action="store_true",
                        help="Send Slack digest of findings")
    args = parser.parse_args()

    # Late import — keep startup fast if just checking args
    from ultralytics import YOLO
    import cv2

    model_name = f"yolo26{args.model}.pt"
    print(f"🔭 Rook Mac Inference — loading {model_name}...")

    # Use MPS on Apple Silicon, fall back to CPU
    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        device = "cpu"

    model = YOLO(model_name)
    print(f"   Device: {device} | conf={CONF_THRESHOLD}")

    frames = sorted(UNCLASSIFIED_DIR.glob("*.jpg"))
    if not frames:
        print("   No unclassified frames to process.")
        return

    print(f"   Processing {len(frames)} frames...\n")

    findings = []
    processed = 0

    RECLASSIFIED_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for img_path in frames:
        results = model(str(img_path), conf=CONF_THRESHOLD, device=device, verbose=False)
        classes = [results[0].names[int(c)] for c in results[0].boxes.cls]
        notable = [c for c in classes if c not in SILENT_CLASSES]
        processed += 1

        if notable:
            s = score(notable)
            summary = ", ".join(sorted(set(notable)))
            print(f"   ✅ {img_path.name}: {summary} (score={s})")
            findings.append({"file": img_path.name, "classes": notable, "score": s})

            if not args.dry_run:
                # Save the RAW frame to reclassified/ — llm_autolabel.py consumes
                # this directory as training imagery, and drawn boxes would poison
                # the dataset. The annotated copy goes to annotated/ for human review.
                dest = RECLASSIFIED_DIR / img_path.name
                shutil.copy2(str(img_path), dest)
                annotated = results[0].plot()
                import cv2 as _cv2
                annotated_dir = RECLASSIFIED_DIR / "annotated"
                annotated_dir.mkdir(parents=True, exist_ok=True)
                _cv2.imwrite(str(annotated_dir / img_path.name), annotated)
                img_path.unlink()  # remove from unclassified after reclassification
        else:
            if not args.dry_run:
                # Move to processed (auditable, not cluttering unclassified)
                shutil.move(str(img_path), PROCESSED_DIR / img_path.name)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*48}")
    print(f"🔭 Rook Mac Inference Complete")
    print(f"   Frames processed : {processed}")
    print(f"   New detections   : {len(findings)}")
    if findings:
        top = sorted(findings, key=lambda x: x["score"], reverse=True)[:5]
        print(f"   Top finds:")
        for f in top:
            print(f"      {f['file']}: {', '.join(set(f['classes']))} (score={f['score']})")
    print(f"{'='*48}\n")

    # ── Optional Slack digest ─────────────────────────────────────────────────
    if args.slack and findings and not args.dry_run:
        top_classes = [c for f in findings for c in f["classes"]]
        class_counts = {c: top_classes.count(c) for c in set(top_classes)}
        summary_line = "  ".join(f"{c} ×{n}" for c, n in
                                  sorted(class_counts.items(), key=lambda x: -x[1]))
        msg = (f"🔭 *Rook Mac Re-inference* — {len(findings)}/{processed} frames had detections\n"
               f"   {summary_line}\n"
               f"   Annotated frames saved to `archive/reclassified/`")
        send_slack(msg)
        print("💬 Slack digest sent.")


if __name__ == "__main__":
    main()
