#!/usr/bin/env python3
"""
train_custom_model.py — fine-tune YOLO26n on the auto-labeled Rook dataset

Consumes the dataset produced by llm_autolabel.py (COCO 0-79 + custom IDs 80+),
transfer-learns from yolo26n.pt at the deployment resolution (imgsz=1088),
gates the release on validation metrics, exports NCNN, and writes a
model_card.json manifest for auditability (and the future v2 dashboard's
model_versions view).

Release gate — the export is REFUSED unless:
  1. every custom class with val examples reaches --min-custom-map mAP50, and
  2. base COCO classes have not regressed more than --max-base-regression
     against the previous model card (skipped if no previous card exists).

The nano architecture is non-negotiable: the Pi 5 budget is ~150ms/frame.

Usage:
    python3 train_custom_model.py                    # train, gate, export
    python3 train_custom_model.py --epochs 150
    python3 train_custom_model.py --force-export     # bypass the gate (not recommended)

Output:
    <archive>/models/rook26n_vNNN/                   # NCNN dir, ready to deploy
    <archive>/models/rook26n_vNNN/model_card.json
"""

import json
import argparse
import shutil
from pathlib import Path
from datetime import datetime

ARCHIVE_DIR = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"
DATASET     = ARCHIVE_DIR / "autolabel" / "dataset.yaml"
MODELS_DIR  = ARCHIVE_DIR / "models"

# Must match llm_autolabel.py — IDs 80+ in this order. This is the model
# contract that rook_engine.py relies on (it reads model.names at load time).
CUSTOM_CLASSES = [
    # Vehicles
    "trash_truck", "street_sweeper",
    "ups_truck", "fedex_truck", "amazon_van", "usps_truck", "dhl_van",
    "school_bus", "police_car", "fire_truck", "ambulance",
    # People
    "baseball_player",
    # Wildlife
    "coyote", "fox", "deer", "raccoon", "opossum", "skunk",
    "squirrel", "rabbit", "wild_turkey", "canada_goose",
    "raptor", "cardinal", "blue_jay",
    # Natural phenomena
    "downed_tree", "smoke", "flood",
    # Curbside & service (append-only — IDs are a contract with existing data)
    "trash_bins", "work_truck",
]


def next_version() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    versions = [int(p.name.split("_v")[-1]) for p in MODELS_DIR.glob("rook26n_v*")
                if p.name.split("_v")[-1].isdigit()]
    return max(versions, default=0) + 1


def latest_model_card():
    cards = sorted(MODELS_DIR.glob("rook26n_v*/model_card.json"))
    if not cards:
        return None
    with open(cards[-1]) as f:
        return json.load(f)


def per_class_map50(val_results, names) -> dict:
    """Extract {class_name: mAP50} for classes present in the val set."""
    out = {}
    for idx, ap in zip(val_results.box.ap_class_index, val_results.box.ap50):
        out[names[int(idx)]] = round(float(ap), 4)
    return out


def main():
    parser = argparse.ArgumentParser(description="Rook custom model fine-tune")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1088,
                        help="must match deployment resolution")
    parser.add_argument("--base", default="yolo26n.pt",
                        help="base checkpoint (nano only — Pi 5 latency budget)")
    parser.add_argument("--min-custom-map", type=float, default=0.60,
                        help="min mAP50 per custom class with val examples")
    parser.add_argument("--max-base-regression", type=float, default=0.05,
                        help="max allowed mAP50 drop per base class vs previous card")
    parser.add_argument("--force-export", action="store_true",
                        help="export even if the release gate fails")
    parser.add_argument("--data", default=str(DATASET),
                        help="dataset.yaml path (use a local-disk copy — training "
                             "reads through Dropbox File Provider fail intermittently)")
    args = parser.parse_args()

    dataset = Path(args.data)
    if not dataset.exists():
        raise SystemExit(f"❌ No dataset at {dataset} — run llm_autolabel.py first.")

    from ultralytics import YOLO
    try:
        import torch
        device = ("cuda" if torch.cuda.is_available()
                  else "mps" if torch.backends.mps.is_available() else "cpu")
    except Exception:
        device = "cpu"

    version = next_version()
    print(f"🎓 Rook fine-tune v{version:03d} — base={args.base}, device={device}, "
          f"imgsz={args.imgsz}, epochs={args.epochs}")

    model = YOLO(args.base)
    model.train(data=str(dataset), epochs=args.epochs, imgsz=args.imgsz,
                device=device, project=str(MODELS_DIR / "runs"),
                name=f"rook26n_v{version:03d}")

    # ── Validate & gate ───────────────────────────────────────────────────────
    best = Path(model.trainer.best)
    trained = YOLO(str(best))
    val = trained.val(data=str(dataset), imgsz=args.imgsz, device=device)
    class_map = per_class_map50(val, trained.names)

    failures = []
    custom_in_val = {c: m for c, m in class_map.items() if c in CUSTOM_CLASSES}
    if not custom_in_val:
        failures.append("no custom-class examples in the val split — dataset too small")
    for cls, m in custom_in_val.items():
        if m < args.min_custom_map:
            failures.append(f"{cls} mAP50 {m:.3f} < {args.min_custom_map}")

    prev_card = latest_model_card()
    base_regressions = {}
    if prev_card:
        prev_map = prev_card.get("class_map50", {})
        for cls, m in class_map.items():
            if cls in CUSTOM_CLASSES or cls not in prev_map:
                continue
            drop = prev_map[cls] - m
            if drop > args.max_base_regression:
                base_regressions[cls] = round(drop, 4)
                failures.append(f"base class '{cls}' regressed {drop:.3f} mAP50")

    print("\n── Release gate ──")
    for cls, m in sorted(custom_in_val.items()):
        print(f"   custom  {cls:<18} mAP50={m:.3f}")
    if failures:
        print("   ❌ GATE FAILED:")
        for f_ in failures:
            print(f"      - {f_}")
        if not args.force_export:
            raise SystemExit("   Export refused. Grow the dataset (more archive frames / "
                             "another llm_autolabel.py pass) or re-run with --force-export.")
        print("   ⚠️  --force-export set — exporting anyway.")
    else:
        print("   ✅ Gate passed.")

    # ── NCNN export (mandatory for Pi CPU performance) ───────────────────────
    trained.export(format="ncnn", imgsz=args.imgsz)
    ncnn_src = best.parent / f"{best.stem}_ncnn_model"

    dest = MODELS_DIR / f"rook26n_v{version:03d}"
    dest.mkdir(parents=True, exist_ok=True)
    ncnn_dest = dest / "ncnn_model"
    if ncnn_dest.exists():
        shutil.rmtree(ncnn_dest)
    shutil.move(str(ncnn_src), str(ncnn_dest))
    shutil.copy2(best, dest / "best.pt")

    # ── Model card — provenance for audit + future dashboard ─────────────────
    dataset_manifest = {}
    manifest_path = DATASET.parent / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            dataset_manifest = json.load(f)

    card = {
        "version": version,
        "trained": datetime.now().isoformat(timespec="seconds"),
        "base_model": args.base,
        "imgsz": args.imgsz,
        "epochs": args.epochs,
        "custom_classes": CUSTOM_CLASSES,
        "map50_overall": round(float(val.box.map50), 4),
        "class_map50": class_map,
        "gate": {"passed": not failures, "failures": failures,
                 "forced": bool(failures and args.force_export),
                 "base_regressions": base_regressions},
        "dataset": dataset_manifest,
    }
    with open(dest / "model_card.json", "w") as f:
        json.dump(card, f, indent=2)

    print(f"\n✅ Model v{version:03d} ready:")
    print(f"   {ncnn_dest}")
    print(f"   Deploy with: bash app/deploy_model_to_pi.sh {version:03d}")


if __name__ == "__main__":
    main()
