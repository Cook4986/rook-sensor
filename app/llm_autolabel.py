#!/usr/bin/env python3
"""
llm_autolabel.py — LLM-assisted auto-labeling of the Rook archive (Mac-side)

Turns the unclassified/reclassified archive into a YOLO training dataset with
ZERO manual annotation, using a two-model split:

  Stage A  Teacher detector (YOLO26l/x, conf=0.20) draws bounding boxes —
           detectors localize well; VLMs don't.
  Stage B  A vision LLM classifies crops of refinable classes (truck, car,
           bus, person) into fine-grained local classes (trash_truck,
           ups_truck, fedex_truck, amazon_van, usps_truck, baseball_player)
           via a closed-vocabulary prompt with a "none" escape hatch —
           VLMs excel at crop classification.
  Stage C  Emits a standard YOLO dataset: COCO IDs 0-79 preserved, custom
           classes appended as IDs 80-85, processed/ frames included as
           empty-label background negatives (anti-hallucination).

Privacy: runs entirely on the owner's Mac against the owner's own Dropbox
archive. Crops are sent to the configured LLM API only when LLM_API_KEY is
set; otherwise Stage B is skipped and the dataset uses teacher labels only.
Nothing here runs on (or transmits from) the Pi.

Cost control: every VLM verdict is cached by crop content hash in
autolabel_cache.jsonl — a crop is billed at most once, ever. Re-runs are
incremental and idempotent.

Usage:
    python3 llm_autolabel.py                  # label everything new
    python3 llm_autolabel.py --dry-run        # report without writing dataset
    python3 llm_autolabel.py --max-crops 200  # cap LLM calls this run
    python3 llm_autolabel.py --slack          # send Slack digest when done

Config (~/.env, Mac-side):
    LLM_API_BASE=https://api.openai.com/v1    # any OpenAI-compatible endpoint
    LLM_API_KEY=sk-...                        # unset → Stage B skipped
    LLM_MODEL=gpt-4o-mini                     # vision-capable chat model
    LLM_MIN_CONFIDENCE=0.8                    # below this → keep COCO label
"""

import os
import re
import json
import base64
import hashlib
import argparse
import random
from pathlib import Path
from datetime import datetime

import httpx
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv(Path.home() / ".env")

ARCHIVE_DIR      = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"
UNCLASSIFIED_DIR = ARCHIVE_DIR / "unclassified"
RECLASSIFIED_DIR = ARCHIVE_DIR / "reclassified"
PROCESSED_DIR    = ARCHIVE_DIR / "processed"      # hard negatives (verified empty)
DATASET_DIR      = ARCHIVE_DIR / "autolabel"      # output YOLO dataset
CACHE_FILE       = DATASET_DIR / "autolabel_cache.jsonl"

LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")
LLM_MODEL    = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_MIN_CONF = float(os.environ.get("LLM_MIN_CONFIDENCE", "0.8"))

# Teacher proposals: same low threshold as reclassify_archive.py to catch
# faint/distant subjects; boxes NOT promoted by the VLM must clear a higher
# bar before entering the dataset (teacher false positives are poison).
TEACHER_CONF          = 0.20
TEACHER_KEEP_MIN_CONF = 0.35

# COCO class → candidate custom subclasses the VLM may promote it to.
# Closed vocabulary per parent class keeps the LLM honest.
REFINABLE = {
    "truck":  ["trash_truck", "ups_truck", "fedex_truck", "amazon_van", "usps_truck"],
    "bus":    ["trash_truck", "ups_truck", "fedex_truck", "amazon_van", "usps_truck"],
    "car":    ["amazon_van", "usps_truck"],   # vans/LLVs sometimes classify as car
    "person": ["baseball_player"],
}

# Custom classes appended after COCO's 80 — order is the model contract.
# rook_engine.py and train_custom_model.py must agree with this list.
CUSTOM_CLASSES = ["trash_truck", "ups_truck", "fedex_truck",
                  "amazon_van", "usps_truck", "baseball_player"]

VISUAL_CUES = {
    "trash_truck":     "garbage/recycling truck: rear or side loader, hopper, municipal livery",
    "ups_truck":       "UPS: brown package car or brown/gold semi, UPS shield logo",
    "fedex_truck":     "FedEx: white body with purple/orange or purple/green FedEx wordmark",
    "amazon_van":      "Amazon: blue-grey Sprinter/Transit van, Prime smile-arrow logo",
    "usps_truck":      "USPS: white LLV/ProMaster, blue eagle logo, red-blue stripe",
    "baseball_player": "person in baseball uniform: cap, jersey, baseball pants, glove or bat",
}

VAL_FRACTION = 0.15   # deterministic per-image split (hash-based, stable across runs)


# ── VLM crop classification ───────────────────────────────────────────────────
def _crop_prompt(parent_class: str, candidates: list) -> str:
    cues = "\n".join(f"- {c}: {VISUAL_CUES[c]}" for c in candidates)
    return (
        f"This image is a crop of an object a detector classified as '{parent_class}', "
        f"from a fixed yard-monitoring camera.\n"
        f"Decide if it is specifically one of:\n{cues}\n"
        f"If it clearly matches one, answer with that label. If it does not match, "
        f"or the crop is too small/blurry/ambiguous to be sure, answer 'none'.\n"
        f'Respond with ONLY this JSON: {{"label": "<candidate-or-none>", '
        f'"confidence": <0.0-1.0>}}'
    )


def _parse_verdict(text: str, candidates: list):
    """Strict-ish parse: accept the first JSON object found; validate vocabulary."""
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        label = str(data.get("label", "none")).strip().lower()
        conf = float(data.get("confidence", 0.0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if label != "none" and label not in candidates:
        return None
    return {"label": label, "confidence": max(0.0, min(1.0, conf))}


def classify_crop(client: httpx.Client, crop_jpg: bytes, parent_class: str, candidates: list):
    """One VLM round-trip. Returns {"label", "confidence"} or None on failure.

    Malformed responses are retried once, then dropped (caller falls back to
    the COCO label) — a bad label is worse than no label.
    """
    b64 = base64.b64encode(crop_jpg).decode()
    payload = {
        "model": LLM_MODEL,
        "temperature": 0,
        "max_tokens": 60,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _crop_prompt(parent_class, candidates)},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
            ],
        }],
    }
    for _ in range(2):
        try:
            r = client.post(f"{LLM_API_BASE}/chat/completions", json=payload, timeout=60)
            r.raise_for_status()
            verdict = _parse_verdict(r.json()["choices"][0]["message"]["content"], candidates)
            if verdict is not None:
                return verdict
        except httpx.HTTPError:
            pass
    return None


# ── Verdict cache (content-addressed — each crop billed once, ever) ──────────
def load_cache() -> dict:
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    cache[rec["hash"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue
    return cache


def append_cache(record: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Dataset assembly ──────────────────────────────────────────────────────────
def split_of(img_path: Path) -> str:
    """Deterministic train/val assignment — stable as the archive grows."""
    h = int(hashlib.sha1(img_path.name.encode()).hexdigest(), 16)
    return "val" if (h % 1000) < VAL_FRACTION * 1000 else "train"


def write_sample(img_path: Path, labels: list, split: str):
    """Copy image + write YOLO label file (empty list → background negative)."""
    import shutil
    img_dst = DATASET_DIR / "images" / split / img_path.name
    lbl_dst = DATASET_DIR / "labels" / split / (img_path.stem + ".txt")
    img_dst.parent.mkdir(parents=True, exist_ok=True)
    lbl_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(img_path, img_dst)
    lbl_dst.write_text("".join(f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n"
                               for c, x, y, w, h in labels))


def write_data_yaml(coco_names: dict):
    """dataset.yaml: COCO 0-79 preserved, custom classes appended as 80-85."""
    lines = [f"path: {DATASET_DIR}", "train: images/train", "val: images/val", "", "names:"]
    for i in range(80):
        lines.append(f"  {i}: {coco_names[i]}")
    for j, name in enumerate(CUSTOM_CLASSES):
        lines.append(f"  {80 + j}: {name}")
    (DATASET_DIR / "dataset.yaml").write_text("\n".join(lines) + "\n")


def send_slack(text: str):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        try:
            httpx.post(url, json={"text": text}, timeout=5)
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rook LLM auto-labeling pass")
    parser.add_argument("--model", default="l", choices=["n", "s", "m", "l", "x"],
                        help="teacher YOLO26 size (default: l)")
    parser.add_argument("--max-crops", type=int, default=1000,
                        help="max NEW crops sent to the LLM this run (cost budget)")
    parser.add_argument("--max-negatives", type=int, default=300,
                        help="max background negatives from processed/")
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and report, but write no dataset files")
    parser.add_argument("--slack", action="store_true",
                        help="send Slack digest of labeling results")
    args = parser.parse_args()

    # Late imports — keep --help fast
    from ultralytics import YOLO
    import cv2

    if not LLM_API_KEY:
        print("⚠️  LLM_API_KEY not set — Stage B (VLM refinement) will be SKIPPED.")
        print("   Dataset will contain teacher labels only (no custom classes).")

    print(f"🏷️  Rook Auto-Label — teacher yolo26{args.model}.pt, VLM {LLM_MODEL}")
    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        device = "cpu"
    teacher = YOLO(f"yolo26{args.model}.pt")
    coco_names = teacher.names
    name_to_id = {v: k for k, v in coco_names.items()}
    custom_id = {name: 80 + i for i, name in enumerate(CUSTOM_CLASSES)}

    frames = sorted(set(UNCLASSIFIED_DIR.glob("*.jpg")) | set(RECLASSIFIED_DIR.glob("*.jpg")))
    if not frames:
        print("   No archive frames to process.")
        return

    cache = load_cache()
    client = httpx.Client(headers={"Authorization": f"Bearer {LLM_API_KEY}"})

    llm_calls = 0
    stats = {"frames": 0, "boxes": 0, "promoted": {}, "cache_hits": 0, "llm_failures": 0}

    print(f"   Processing {len(frames)} frames (cache: {len(cache)} verdicts)...\n")

    for img_path in frames:
        results = teacher(str(img_path), conf=TEACHER_CONF, device=device, verbose=False)
        boxes = results[0].boxes
        img_h, img_w = results[0].orig_shape
        frame = cv2.imread(str(img_path))
        labels = []

        for i, cls_id in enumerate(boxes.cls):
            cls_name = coco_names[int(cls_id)]
            conf = float(boxes.conf[i])
            x1, y1, x2, y2 = (int(v) for v in boxes.xyxy[i].tolist())
            final_id = None

            if cls_name in REFINABLE and LLM_API_KEY:
                crop = frame[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
                if crop.size == 0 or min(crop.shape[:2]) < 24:
                    verdict = None   # too small to classify — keep COCO label
                else:
                    # Cap upload size — 512px longest edge is plenty for livery ID
                    scale = 512 / max(crop.shape[:2])
                    if scale < 1.0:
                        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)),
                                                 max(1, int(crop.shape[0] * scale))))
                    ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    crop_hash = hashlib.sha256(jpg.tobytes()).hexdigest() if ok else None

                    verdict = None
                    if crop_hash and crop_hash in cache:
                        verdict = cache[crop_hash]
                        stats["cache_hits"] += 1
                    elif crop_hash and llm_calls < args.max_crops:
                        verdict = classify_crop(client, jpg.tobytes(), cls_name,
                                                REFINABLE[cls_name])
                        llm_calls += 1
                        if verdict is None:
                            stats["llm_failures"] += 1
                        else:
                            append_cache({"hash": crop_hash, "parent": cls_name,
                                          "source": img_path.name, **verdict})
                            cache[crop_hash] = verdict

                if (verdict and verdict["label"] != "none"
                        and verdict["confidence"] >= LLM_MIN_CONF):
                    final_id = custom_id[verdict["label"]]
                    stats["promoted"][verdict["label"]] = \
                        stats["promoted"].get(verdict["label"], 0) + 1

            if final_id is None:
                # Not promoted: keep the COCO label, but only if the teacher
                # itself is reasonably sure — low-conf proposals existed solely
                # as VLM candidates and would poison the dataset as-is.
                if conf < TEACHER_KEEP_MIN_CONF:
                    continue
                final_id = int(cls_id)

            # YOLO format: class cx cy w h, normalized
            labels.append((final_id,
                           ((x1 + x2) / 2) / img_w, ((y1 + y2) / 2) / img_h,
                           (x2 - x1) / img_w, (y2 - y1) / img_h))
            stats["boxes"] += 1

        if labels and not args.dry_run:
            write_sample(img_path, labels, split_of(img_path))
        if labels:
            stats["frames"] += 1

    # ── Background negatives: verified-empty frames teach "nothing here" ─────
    negatives = sorted(PROCESSED_DIR.glob("*.jpg"))
    random.seed(0)   # reproducible subset
    if len(negatives) > args.max_negatives:
        negatives = random.sample(negatives, args.max_negatives)
    if not args.dry_run:
        for img_path in negatives:
            write_sample(img_path, [], split_of(img_path))
        write_data_yaml(coco_names)
        manifest = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "teacher": f"yolo26{args.model}.pt", "teacher_conf": TEACHER_CONF,
            "vlm": LLM_MODEL if LLM_API_KEY else None,
            "vlm_min_confidence": LLM_MIN_CONF,
            "labeled_frames": stats["frames"], "boxes": stats["boxes"],
            "negatives": len(negatives), "promoted": stats["promoted"],
            "llm_calls": llm_calls, "cache_hits": stats["cache_hits"],
        }
        with open(DATASET_DIR / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

    # ── Summary ───────────────────────────────────────────────────────────────
    promoted_total = sum(stats["promoted"].values())
    print(f"\n{'=' * 48}")
    print(f"🏷️  Rook Auto-Label Complete{' (dry run)' if args.dry_run else ''}")
    print(f"   Frames labeled    : {stats['frames']}")
    print(f"   Boxes written     : {stats['boxes']}")
    print(f"   Custom promotions : {promoted_total}")
    for name, n in sorted(stats["promoted"].items(), key=lambda x: -x[1]):
        print(f"      {name}: {n}")
    print(f"   Background negs   : {len(negatives)}")
    print(f"   LLM calls / cache : {llm_calls} / {stats['cache_hits']}")
    if stats["llm_failures"]:
        print(f"   LLM failures      : {stats['llm_failures']} (kept COCO labels)")
    if not args.dry_run:
        print(f"   Dataset           : {DATASET_DIR}/dataset.yaml")
    print(f"{'=' * 48}\n")

    if args.slack and not args.dry_run:
        summary = "  ".join(f"{k} ×{v}" for k, v in
                            sorted(stats["promoted"].items(), key=lambda x: -x[1]))
        send_slack(f"🏷️ *Rook Auto-Label* — {stats['frames']} frames → "
                   f"{stats['boxes']} boxes, {promoted_total} custom promotions\n"
                   f"   {summary or 'no custom classes found'}\n"
                   f"   LLM calls: {llm_calls} (cache hits: {stats['cache_hits']}) — "
                   f"dataset at `archive/autolabel/`")
        print("💬 Slack digest sent.")


if __name__ == "__main__":
    main()
