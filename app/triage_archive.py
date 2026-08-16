#!/usr/bin/env python3
"""
triage_archive.py — Rook Data Mining Pipeline

Runs YOLO26x (or yolo11x) on all unclassified frames to mine training data.
Frames are sorted into candidate directories based on their detections, preparing
them for upload to Roboflow for annotation and fine-tuning.

Usage:
    python3 triage_archive.py              # sort frames into candidate folders
    python3 triage_archive.py --dry-run    # print findings without moving files
"""

import os
import argparse
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")

ARCHIVE_DIR      = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"
UNCLASSIFIED_DIR = ARCHIVE_DIR / "unclassified"

# Target directories for sorting training data
SORT_DIRS = {
    "vehicles": ARCHIVE_DIR / "training_data/candidate_vehicles",
    "people":   ARCHIVE_DIR / "training_data/candidate_people",
    "wildlife": ARCHIVE_DIR / "training_data/candidate_wildlife",
    "empty":    ARCHIVE_DIR / "training_data/candidate_background",
}

# Threshold: we want to catch faint/distant objects for annotation
CONF_THRESHOLD = 0.15

def get_category(classes):
    """Map detected COCO classes to a training bucket."""
    cls_set = set(classes)
    
    # Priority 1: Wildlife
    if cls_set.intersection({"bird", "dog", "cat", "bear", "horse", "sheep", "cow"}):
        return "wildlife"
        
    # Priority 2: Vehicles (the main goal for fine-tuning)
    if cls_set.intersection({"truck", "car", "bus", "motorcycle"}):
        return "vehicles"
        
    # Priority 3: People & activity
    if cls_set.intersection({"person", "bicycle", "sports ball"}):
        return "people"
        
    return None

def main():
    parser = argparse.ArgumentParser(description="Mine training data from Rook archive")
    parser.add_argument("--model", default="x", choices=["n", "s", "m", "l", "x"],
                        help="YOLO26 model size (default: x)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print findings without copying files")
    args = parser.parse_args()

    from ultralytics import YOLO

    model_name = f"yolo26{args.model}.pt"
    print(f"⛏️  Rook Data Mining — loading {model_name}...")

    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        device = "cpu"

    # Try YOLO26 first, fallback to YOLO11
    try:
        model = YOLO(model_name)
    except Exception:
        model_name = f"yolo11{args.model}.pt"
        print(f"   Falling back to {model_name}...")
        model = YOLO(model_name)

    print(f"   Device: {device} | conf={CONF_THRESHOLD}")

    frames = sorted(UNCLASSIFIED_DIR.glob("*.jpg"))
    if not frames:
        print("   No unclassified frames to process.")
        return

    print(f"   Mining {len(frames)} frames...\n")

    if not args.dry_run:
        for d in SORT_DIRS.values():
            d.mkdir(parents=True, exist_ok=True)

    counts = {k: 0 for k in SORT_DIRS.keys()}
    processed = 0

    # Process in batches to avoid overwhelming memory
    for img_path in frames:
        results = model(str(img_path), conf=CONF_THRESHOLD, device=device, verbose=False)
        classes = [results[0].names[int(c)] for c in results[0].boxes.cls]
        
        processed += 1
        
        category = get_category(classes)
        
        if category:
            counts[category] += 1
            summary = ", ".join(sorted(set(classes)))
            print(f"   [{category.upper()}] {img_path.name}: {summary}")
            
            if not args.dry_run:
                # Copy instead of move, so we don't break the original archive
                shutil.copy2(str(img_path), SORT_DIRS[category] / img_path.name)
        else:
            # If absolutely nothing detected even at 0.15, it's a good background image candidate
            # We only sample 10% of these to avoid overwhelming the dataset with empty frames
            if processed % 10 == 0:
                counts["empty"] += 1
                if not args.dry_run:
                    shutil.copy2(str(img_path), SORT_DIRS["empty"] / img_path.name)

    print(f"\n{'='*48}")
    print(f"⛏️  Mining Complete")
    print(f"   Frames processed : {processed}")
    for k, v in counts.items():
        print(f"   {k.capitalize():12} : {v} frames")
    if not args.dry_run:
        print(f"\n   Data sorted into: {ARCHIVE_DIR}/training_data/")
    print(f"{'='*48}\n")

if __name__ == "__main__":
    main()
