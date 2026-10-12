#!/usr/bin/env python3
"""
train_custom_model.py — fine-tune YOLO26n on the auto-labeled Rook dataset

Consumes a local approved snapshot (COCO 0-79 + custom IDs 80+),
transfer-learns from yolo26n.pt at the deployment resolution (imgsz=1088),
gates the release on validation metrics, exports NCNN, and writes a
model_card.json manifest for auditability (and the future v2 dashboard's
model_versions view).

Release gate — the export is REFUSED unless:
  1. every enabled custom class reaches --min-custom-map mAP50, and
  2. generic validation classes do not regress beyond --max-base-regression
     against a stock baseline bound to the same snapshot/checkpoint/resolution.
Source/label approvals and 35 train / 15 validation visits per enabled class are
required before loading a model. These are data minima, not proof of accuracy.

The nano architecture is non-negotiable: the Pi 5 budget is ~150ms/frame.

Usage:
    python3 train_custom_model.py                    # train, gate, export
    python3 train_custom_model.py --epochs 150
    python3 train_custom_model.py --data /local/snapshot/dataset.yaml --verify-only

Output:
    <archive>/models/rook26n_vNNN/                   # NCNN dir, ready to deploy
    <archive>/models/rook26n_vNNN/model_card.json
"""

import json
import argparse
import shutil
import hashlib
import math
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


def validate_baseline(path, evidence, base, imgsz=1088):
    from archive_catalog import require_local
    data = json.loads(require_local(path).read_text())
    if data.get('imgsz') != imgsz:
        raise ValueError('Baseline resolution differs from the training/evaluation resolution')
    if data.get('snapshot_sha256') != evidence['manifest_sha256']:
        raise ValueError('Baseline was evaluated on a different snapshot')
    if data.get('base_sha256') != hashlib.sha256(require_local(base).read_bytes()).hexdigest():
        raise ValueError('Baseline checkpoint differs from the selected base')
    metrics = data.get('class_map50', {})
    for cls in evidence['base_classes_in_val']:
        value = metrics.get(cls)
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f'Missing or invalid stock baseline metric: {cls}')
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Rook custom model fine-tune")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1088,
                        help="must match deployment resolution")
    parser.add_argument("--base", default="yolo26n.pt",
                        help="base checkpoint (nano only — Pi 5 latency budget)")
    parser.add_argument("--min-custom-map", type=float, default=0.60,
                        help="min mAP50 for every enabled custom class")
    parser.add_argument("--max-base-regression", type=float, default=0.05,
                        help="max allowed generic-class mAP50 drop vs the bound stock baseline")
    parser.add_argument('--baseline', type=Path, help='stock evaluation bound to this snapshot/checkpoint')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--verify-only', action='store_true', help='check approvals/coverage without loading a model')
    modes.add_argument('--baseline-only', action='store_true', help='evaluate the stock checkpoint without training')
    parser.add_argument('--runs-dir', type=Path, default=Path.home()/'rook-local/training-runs')
    parser.add_argument("--data", default=str(DATASET),
                        help="approved local snapshot dataset.yaml; legacy auto-label datasets are refused")
    args = parser.parse_args()
    if not (0 < args.min_custom_map <= 1 and 0 <= args.max_base_regression <= 1):
        parser.error('Release thresholds must be finite values between 0 and 1')

    from approved_dataset import verify_snapshot, discard_label_caches
    from archive_catalog import require_local
    try:
        dataset = require_local(args.data)
        evidence = verify_snapshot(dataset)
        runs_dir = require_local(args.runs_dir)
        if args.verify_only:
            print(json.dumps(evidence, indent=2)); return
        base = require_local(args.base)
        if not base.is_file(): raise ValueError('Base checkpoint must already exist on local disk')
        baseline_path = args.baseline or dataset.parent/'stock_baseline.json'
        baseline_map = None if args.baseline_only else validate_baseline(baseline_path, evidence, base, args.imgsz)
        discard_label_caches(dataset)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'Training preflight refused: {exc}') from exc

    from ultralytics import YOLO
    try:
        import torch
        device = ("cuda" if torch.cuda.is_available()
                  else "mps" if torch.backends.mps.is_available() else "cpu")
    except Exception:
        device = "cpu"

    if args.baseline_only:
        from box_review import CLASSES
        baseline_path = require_local(baseline_path)
        if baseline_path.exists(): raise SystemExit('Baseline destination exists; choose a new path')
        model = YOLO(str(base))
        val = model.val(data=str(dataset), imgsz=args.imgsz, device=device,
                        project=str(runs_dir), name='stock_baseline')
        if verify_snapshot(dataset) != evidence:
            raise SystemExit('Snapshot changed during baseline evaluation')
        result = {'snapshot_sha256': evidence['manifest_sha256'],
                  'imgsz': args.imgsz,
                  'base_sha256': hashlib.sha256(base.read_bytes()).hexdigest(),
                  'class_map50': per_class_map50(val, dict(enumerate(CLASSES)))}
        baseline_path.write_text(json.dumps(result, indent=2)+'\n')
        validate_baseline(baseline_path, evidence, base, args.imgsz)
        print(f'Stock baseline saved: {baseline_path}'); return

    version = next_version()
    print(f"🎓 Rook fine-tune v{version:03d} — base={args.base}, device={device}, "
          f"imgsz={args.imgsz}, epochs={args.epochs}")

    model = YOLO(str(base))
    model.train(data=str(dataset), epochs=args.epochs, imgsz=args.imgsz,
                device=device, project=str(runs_dir),
                name=f"rook26n_v{version:03d}")

    # ── Validate & gate ───────────────────────────────────────────────────────
    best = Path(model.trainer.best)
    trained = YOLO(str(best))
    val = trained.val(data=str(dataset), imgsz=args.imgsz, device=device,
                      project=str(runs_dir), name=f'rook26n_v{version:03d}_validation')
    class_map = per_class_map50(val, trained.names)
    if verify_snapshot(dataset) != evidence:
        raise SystemExit('Snapshot changed during training/evaluation; export refused')

    failures = []
    custom_in_val = {c: m for c, m in class_map.items() if c in CUSTOM_CLASSES}
    if not custom_in_val:
        failures.append("no custom-class examples in the val split — dataset too small")
    for cls in evidence['enabled_classes']:
        m = class_map.get(cls)
        if m is None or not math.isfinite(m):
            failures.append(f'enabled class {cls} has no validation metric'); continue
        if m < args.min_custom_map:
            failures.append(f"{cls} mAP50 {m:.3f} < {args.min_custom_map}")

    base_regressions = {}
    for cls in evidence['base_classes_in_val']:
        if cls not in class_map or not math.isfinite(class_map[cls]):
            failures.append(f'generic class {cls} has no validation metric'); continue
        drop = baseline_map[cls] - class_map[cls]
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
        raise SystemExit('Export refused. Improve the reviewed dataset and re-evaluate.')
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
    manifest_path = dataset.parent / "approved_snapshot.json"
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
                 "base_regressions": base_regressions},
        'approved_snapshot': evidence,
        'enabled_classes': evidence['enabled_classes'],
        "dataset": dataset_manifest,
    }
    with open(dest / "model_card.json", "w") as f:
        json.dump(card, f, indent=2)

    print(f"\n✅ Model v{version:03d} ready:")
    print(f"   {ncnn_dest}")
    print(f"   Deploy with: bash app/deploy_model_to_pi.sh {version:03d}")


if __name__ == "__main__":
    main()
