#!/usr/bin/env python3
"""
review_custom_labels.py — human review pass for VLM-promoted custom labels

Phase 1 of the post-mortem plan for the failed rook26n_v001 fine-tune: every
custom-class box (IDs 80+) in the auto-labeled dataset gets rendered as a
single review image — annotated full frame on the left, zoomed crop on the
right — grouped into per-class folders for fast visual triage.

Workflow:
    1. python3 review_custom_labels.py
       Builds <archive>/autolabel/review/<class>/*.jpg and review_manifest.json.
    2. Eyeball the folders in Finder. Drag every FALSE POSITIVE into
       <archive>/autolabel/review/rejected/  (keep good ones where they are).
       Individual images or whole class folders both work.
    3. python3 review_custom_labels.py --apply
       For each rejected image:
         - removes that box line from the dataset label file (a frame whose
           label file becomes empty stays in the dataset as a background
           negative — correct, since the frame is genuinely empty), and
         - appends an overriding "none" verdict to autolabel_cache.jsonl so
           future llm_autolabel.py runs cannot re-promote the same crop/frame
           (the cache loader is last-record-wins).

Candidate mode — mine the sub-threshold verdicts the pipeline discarded:
    4. python3 review_custom_labels.py --candidates
       The verdict cache keeps every VLM answer, including ones below the
       promotion gate (crop 0.8 / scene 0.85). This renders a gallery under
       review/candidates/<class>/ for verdicts in the 0.60–gate band —
       e.g. the usps_truck and police_car sightings that were seen but never
       promoted. Drag REAL ones into review/approved/ (bad ones into
       review/rejected/), then run --apply. Approvals are pinned in the cache
       at confidence 1.0, so the next llm_autolabel.py pass promotes them
       into the dataset with proper teacher boxes; rejections are pinned to
       "none" and never resurface.

Requires only Pillow — no cv2/ultralytics, so it runs in any Python.
"""

import re
import json
import shutil
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ARCHIVE_DIR  = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"
DATASET_DIR  = ARCHIVE_DIR / "autolabel"
LABELS_DIR   = DATASET_DIR / "labels"
IMAGES_DIR   = DATASET_DIR / "images"
CACHE_FILE   = DATASET_DIR / "autolabel_cache.jsonl"
REVIEW_DIR   = DATASET_DIR / "review"
REJECTED_DIR = REVIEW_DIR / "rejected"
APPROVED_DIR = REVIEW_DIR / "approved"
APPLIED_DIR  = REVIEW_DIR / "applied"    # processed decisions parked here
MANIFEST     = REVIEW_DIR / "review_manifest.json"
CAND_MANIFEST = REVIEW_DIR / "candidates_manifest.json"

CUSTOM_ID_MIN = 80
ZOOM_MARGIN   = 0.15   # context padding around the box in the zoom panel

# Promotion gates — must match llm_autolabel.py defaults.
CROP_GATE  = 0.80   # Stage B1 (crop verdicts)
SCENE_GATE = 0.85   # Stage B2 (whole-frame verdicts)
CAND_MIN   = 0.60   # below this a verdict is too weak to be worth human time

# Where source archive frames may live (cache records store only filenames).
SOURCE_DIRS = [ARCHIVE_DIR / "unclassified", ARCHIVE_DIR / "reclassified",
               ARCHIVE_DIR / "classified", ARCHIVE_DIR / "processed"]


def class_names() -> dict:
    """Parse id->name from dataset.yaml (avoids a PyYAML dependency)."""
    names = {}
    for line in (DATASET_DIR / "dataset.yaml").read_text().splitlines():
        m = re.match(r"\s+(\d+):\s*(.+?)\s*$", line)
        if m:
            names[int(m.group(1))] = m.group(2)
    return names


def load_cache_confidences() -> dict:
    """(source_filename, label) -> confidence, for display on review images."""
    conf = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    conf[(rec["source"], rec["label"])] = rec.get("confidence")
                except (json.JSONDecodeError, KeyError):
                    continue
    return conf


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:            # older Pillow without size kwarg
        return ImageFont.load_default()


def render_review_image(img_path: Path, box_xyxy: tuple, caption: str) -> Image.Image:
    """Annotated full frame (left) + zoomed crop with context margin (right)."""
    frame = Image.open(img_path).convert("RGB")
    w, h = frame.size
    x1, y1, x2, y2 = box_xyxy

    annotated = frame.copy()
    draw = ImageDraw.Draw(annotated)
    draw.rectangle([x1, y1, x2, y2], outline=(255, 40, 40), width=3)
    font = _font(15)
    tb = draw.textbbox((0, 0), caption, font=font)
    ty = y1 - (tb[3] - tb[1]) - 8 if y1 > (tb[3] - tb[1]) + 8 else y2 + 4
    draw.rectangle([x1, ty, x1 + (tb[2] - tb[0]) + 8, ty + (tb[3] - tb[1]) + 6],
                   fill=(255, 40, 40))
    draw.text((x1 + 4, ty + 2), caption, fill=(255, 255, 255), font=font)

    # Zoom panel: box + margin, scaled to the frame height
    mx = (x2 - x1) * ZOOM_MARGIN
    my = (y2 - y1) * ZOOM_MARGIN
    zx1, zy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    zx2, zy2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
    zoom = frame.crop((zx1, zy1, zx2, zy2))
    scale = h / max(1, zoom.height)
    zoom = zoom.resize((max(1, int(zoom.width * scale)), h), Image.LANCZOS)
    if zoom.width > int(1.5 * h):                       # very wide boxes
        zoom = zoom.resize((int(1.5 * h), h), Image.LANCZOS)

    pad = 6
    combo = Image.new("RGB", (w + pad + zoom.width, h), (24, 24, 24))
    combo.paste(annotated, (0, 0))
    combo.paste(zoom, (w + pad, 0))
    return combo


def generate():
    names = class_names()
    confidences = load_cache_confidences()
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)

    manifest, per_class = {}, {}
    for split in ("train", "val"):
        for lbl_path in sorted((LABELS_DIR / split).glob("*.txt")):
            lines = lbl_path.read_text().splitlines()
            custom = [(i, ln) for i, ln in enumerate(lines)
                      if ln.strip() and int(ln.split()[0]) >= CUSTOM_ID_MIN]
            if not custom:
                continue
            img_path = IMAGES_DIR / split / (lbl_path.stem + ".jpg")
            if not img_path.exists():
                print(f"   ⚠️  {lbl_path.stem}: image missing, skipped")
                continue
            with Image.open(img_path) as probe:
                w, h = probe.size

            for i, ln in custom:
                cls_id, cx, cy, bw, bh = ln.split()
                cls_id = int(cls_id)
                cx, cy, bw, bh = (float(v) for v in (cx, cy, bw, bh))
                cls = names.get(cls_id, str(cls_id))
                x1, y1 = (cx - bw / 2) * w, (cy - bh / 2) * h
                x2, y2 = (cx + bw / 2) * w, (cy + bh / 2) * h

                conf = confidences.get((lbl_path.stem + ".jpg", cls))
                caption = f"{cls}" + (f"  vlm {conf:.2f}" if conf else "")
                review_name = f"{cls}__{split}__{lbl_path.stem}__b{i}.jpg"
                out = REVIEW_DIR / cls / review_name
                out.parent.mkdir(parents=True, exist_ok=True)
                render_review_image(img_path, (x1, y1, x2, y2), caption).save(
                    out, quality=90)

                manifest[review_name] = {"split": split, "stem": lbl_path.stem,
                                         "class_id": cls_id, "class_name": cls,
                                         "line": ln}
                per_class[cls] = per_class.get(cls, 0) + 1

    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)

    total = sum(per_class.values())
    print(f"\n{'=' * 52}")
    print(f"🔍 Review gallery built — {total} custom boxes")
    for cls, n in sorted(per_class.items(), key=lambda x: -x[1]):
        print(f"   {cls:<18} {n}")
    print(f"\n   Gallery : {REVIEW_DIR}")
    print(f"   Each image: full frame (left) + zoomed crop (right).")
    print(f"\n   Next: drag FALSE POSITIVES into {REJECTED_DIR.name}/,")
    print(f"   then run:  python3 {Path(__file__).name} --apply")
    print(f"{'=' * 52}\n")


def load_cache_records() -> list:
    records = []
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            for line in f:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def effective_cache(records: list) -> dict:
    """hash -> latest record (matches llm_autolabel.py's last-record-wins load)."""
    return {r["hash"]: r for r in records if "hash" in r}


def find_source_frame(name: str):
    """Locate an archive frame by filename (cache records store names only)."""
    for d in SOURCE_DIRS:
        p = d / name
        if p.exists():
            return p
    hits = list((ARCHIVE_DIR / "beast_cam").glob(f"*/{name}"))
    if hits:
        return hits[0]
    for split in ("train", "val"):
        p = IMAGES_DIR / split / name
        if p.exists():
            return p
    return None


def generate_candidates():
    """Gallery of sub-gate VLM verdicts — real sightings the pipeline discarded."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)

    manifest, per_class, missing = {}, {}, 0
    for rec in effective_cache(load_cache_records()).values():
        label, conf = rec.get("label"), rec.get("confidence", 0.0)
        if not label or label == "none" or rec.get("reviewed"):
            continue
        gate = SCENE_GATE if rec.get("parent") == "__frame__" else CROP_GATE
        if not (CAND_MIN <= conf < gate):
            continue

        src = find_source_frame(rec.get("source", ""))
        if src is None:
            missing += 1
            continue

        caption = (f"{label}  vlm {conf:.2f} (below {gate:.2f} gate)  "
                   f"parent: {rec.get('parent')}")
        review_name = f"cand__{label}__{rec['hash'][:10]}__{src.stem}.jpg"
        out = REVIEW_DIR / "candidates" / label / review_name
        out.parent.mkdir(parents=True, exist_ok=True)

        if rec.get("parent") == "__frame__" and rec.get("box"):
            with Image.open(src) as probe:
                w, h = probe.size
            bx1, by1, bx2, by2 = rec["box"]
            img = render_review_image(src, (bx1 * w, by1 * h, bx2 * w, by2 * h),
                                      caption)
        else:
            # Crop verdicts don't carry frame coordinates — show the full
            # frame; the subject (usually a vehicle) is easy to spot.
            img = Image.open(src).convert("RGB")
            draw = ImageDraw.Draw(img)
            font = _font(15)
            tb = draw.textbbox((0, 0), caption, font=font)
            draw.rectangle([0, 0, tb[2] + 12, tb[3] + 10], fill=(255, 40, 40))
            draw.text((6, 4), caption, fill=(255, 255, 255), font=font)
        img.save(out, quality=90)

        manifest[review_name] = {"hash": rec["hash"], "label": label,
                                 "parent": rec.get("parent"),
                                 "source": rec.get("source"),
                                 "confidence": conf,
                                 "box": rec.get("box")}
        per_class[label] = per_class.get(label, 0) + 1

    with open(CAND_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'=' * 52}")
    print(f"🕵️  Candidate gallery — {sum(per_class.values())} sub-gate verdicts")
    for cls, n in sorted(per_class.items(), key=lambda x: -x[1]):
        print(f"   {cls:<18} {n}")
    if missing:
        print(f"   (source frame not found for {missing} verdicts — skipped)")
    print(f"\n   Gallery : {REVIEW_DIR / 'candidates'}")
    print(f"   Drag REAL sightings into {APPROVED_DIR.name}/, junk into "
          f"{REJECTED_DIR.name}/,")
    print(f"   then run:  python3 {Path(__file__).name} --apply")
    print(f"   Approvals enter the dataset on the next llm_autolabel.py pass.")
    print(f"{'=' * 52}\n")


def apply_reviews():
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    cand_manifest = (json.loads(CAND_MANIFEST.read_text())
                     if CAND_MANIFEST.exists() else {})
    if not manifest and not cand_manifest:
        raise SystemExit("❌ No review manifests — run a gallery build first.")

    rejected = sorted(REJECTED_DIR.rglob("*.jpg"))   # files or whole class folders
    approved = sorted(APPROVED_DIR.rglob("*.jpg")) if APPROVED_DIR.exists() else []
    if not rejected and not approved:
        raise SystemExit(f"Nothing in {REJECTED_DIR} or {APPROVED_DIR} — "
                         f"sort review images there first.")

    cache_records = load_cache_records()

    def park(path: Path, bucket: str):
        """Move a processed decision into review/applied/ so a later --apply
        run can't double-process it."""
        dest = APPLIED_DIR / bucket / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))

    # Reviewers can RELABEL a candidate by dropping it into a sibling class
    # folder (e.g. a street_sweeper misread moved into trash_truck/): the
    # enclosing folder name wins over the manifest label when it's a known
    # custom class.
    custom_names = {n for i, n in class_names().items() if i >= CUSTOM_ID_MIN}

    def effective_label(path: Path, entry: dict) -> str:
        folder = path.parent.name
        if folder in custom_names and folder != entry["label"]:
            print(f"   🔁 {path.name}: relabeled {entry['label']} → {folder}")
            return folder
        return entry["label"]

    # ── Approvals (candidate gallery only): pin verdict at confidence 1.0 ────
    approved_pins = 0
    with open(CACHE_FILE, "a") as f:
        for app in approved:
            entry = cand_manifest.get(app.name)
            if entry is None:
                print(f"   ⚠️  {app.name}: not a candidate image, skipped")
                continue
            override = {"hash": entry["hash"], "parent": entry["parent"],
                        "source": entry["source"],
                        "label": effective_label(app, entry),
                        "confidence": 1.0, "reviewed": "approved"}
            if entry["parent"] == "__frame__":
                override["box"] = entry.get("box")
            f.write(json.dumps(override) + "\n")
            approved_pins += 1
            park(app, "approved")

    removed, pinned, now_negative = 0, 0, 0
    for rej in rejected:
        # Candidate rejections only need a cache pin — they were never
        # in the dataset to begin with.
        cand = cand_manifest.get(rej.name)
        if cand is not None:
            override = {"hash": cand["hash"], "parent": cand["parent"],
                        "source": cand["source"], "label": "none",
                        "confidence": 1.0, "reviewed": "rejected"}
            if cand["parent"] == "__frame__":
                override["box"] = None
            with open(CACHE_FILE, "a") as f:
                f.write(json.dumps(override) + "\n")
            pinned += 1
            park(rej, "rejected")
            continue

        entry = manifest.get(rej.name)
        if entry is None:
            print(f"   ⚠️  {rej.name}: not in any manifest, skipped")
            continue

        lbl_path = LABELS_DIR / entry["split"] / (entry["stem"] + ".txt")
        lines = lbl_path.read_text().splitlines() if lbl_path.exists() else []
        if entry["line"] in lines:
            lines.remove(entry["line"])         # removes one instance
            lbl_path.write_text("".join(ln + "\n" for ln in lines))
            removed += 1
            if not lines:
                now_negative += 1
        else:
            print(f"   ⚠️  {rej.name}: box already removed from {lbl_path.name}")

        # Pin the cache so re-runs can't re-promote this crop/frame verdict.
        # Loader is last-record-wins, so appending an override is enough.
        source = entry["stem"] + ".jpg"
        matches = [r for r in cache_records
                   if r.get("source") == source and r.get("label") == entry["class_name"]]
        with open(CACHE_FILE, "a") as f:
            for r in matches:
                override = {"hash": r["hash"], "parent": r.get("parent"),
                            "source": source, "label": "none", "confidence": 1.0,
                            "reviewed": "rejected"}
                if r.get("parent") == "__frame__":
                    override["box"] = None
                f.write(json.dumps(override) + "\n")
                pinned += 1
        if not matches:
            print(f"   ⚠️  {rej.name}: no cache record found (label removed, "
                  f"but a re-label run may re-propose it)")
        park(rej, "rejected")

    print(f"\n{'=' * 52}")
    print(f"🧹 Applied {len(rejected)} rejections, {len(approved)} approvals")
    print(f"   Boxes removed from labels : {removed}")
    print(f"   Frames now background negs: {now_negative}")
    print(f"   Cache verdicts pinned none: {pinned}")
    print(f"   Approvals pinned at 1.0   : {approved_pins}")
    if approved_pins:
        print(f"   Approved sightings enter the dataset on the next "
              f"llm_autolabel.py pass.")
    print(f"   Re-run llm_autolabel.py any time — rejected verdicts stay dead.")
    print(f"{'=' * 52}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review VLM-promoted custom labels")
    parser.add_argument("--apply", action="store_true",
                        help="apply rejections/approvals from review/rejected|approved/")
    parser.add_argument("--candidates", action="store_true",
                        help="build gallery of sub-gate cache verdicts for review")
    args = parser.parse_args()
    if args.apply:
        apply_reviews()
    elif args.candidates:
        generate_candidates()
    else:
        generate()
