# Source-bound review and approved training snapshots

Identity triage is valuable evidence, but it does not certify localization or an
empty frame. The new workflow preserves earlier review decisions and requires an
explicit check of every box before those labels can enter training.

## Prepare and review locally

`app/box_review.py` selects confirmed/corrected identities and interesting discovery
frames from the local archive review ledger. It copies only recognized raw collection
paths into `~/rook-local/box-review/images`, names copies by SHA-256, verifies JPEGs
locally and records origin, dimensions, prior human decisions and proposal hashes.
Dropbox access is limited to bounded source copies; model inference reads local disk.
Missing/unreadable sources are reported rather than assumed empty.

An optional existing local stock teacher checkpoint proposes boxes without paid
inference. Custom identity decisions are attached to a proposal only if the recreated
crop's JPEG hash exactly matches the original cached crop. Unmatched identities stay
visible as hints; they are not assigned to an arbitrary object. All boxes, including
generic detections and exact hash matches, still need human verification.

From the repo directory, using the appropriate installed interpreter:

```bash
python3 app/box_review.py --teacher /local/models/yolo26l.pt
python3 app/archive_review.py --boxes --catalog ~/rook-local/box-review
```

The private same-Mac page has separate identity/discovery queues. Change classes,
remove wrong boxes, draw replacements or use numeric coordinates. Check all visible
target objects. Explicitly confirm an empty frame only if it contains none; rejection
of one label is not a negative example. Defer unresolved objects or unavailable classes.

Approvals append to `box_decisions.jsonl` and bind the exact source SHA-256, proposal
hash, canonical normalized boxes and serialized YOLO-label SHA-256. Images are checked
both when served and when a decision is saved. Changed images or stale proposals are
refused. The server retains loopback-only binding, private token/cookie, strict Host
and Origin checks and allowlisted image access. The agent must not submit human
approvals. Back up the ledger without rewriting it; keep raw images/receipts out of git.

## Export a new snapshot

`app/approved_dataset.py` requires an explicit split-plan JSON with `groups_reviewed:
true`, `enabled_classes: ["usps_truck", "trash_truck"]`, and an `items` mapping from
source SHA-256 to `{ "split": "train" | "val", "visit_group": "reviewed-visit-id" }`.
Determine actual visits/near-duplicate groups before making that attestation. Time
buckets are only sampling aids; neither the exporter nor a checked flag proves that
a human grouped near-duplicates correctly. Keep all images from a visit in one split.

```bash
python3 app/approved_dataset.py --package ~/rook-local/box-review \
  --plan /local/reviewed-split-plan.json --output /local/snapshots/pilot-001
```

The destination must be new. The exporter writes exact raw bytes, approved label bytes,
`approved_snapshot.json` with approval evidence, and a self-contained `dataset.yaml`.
Deferred, stale, unreviewed or tampered proposals cannot export. It does not rewrite
legacy cache/dataset artifacts or declare that review automatically establishes release
readiness. Export can produce an approved but undersized snapshot; its final verification
then reports inadequate coverage. Use a new version for any subsequent snapshot.

## Training gates

`train_custom_model.py --data /local/snapshots/pilot-001/dataset.yaml --verify-only`
checks, before model loading:

- All paths resolve locally; class order matches COCO plus the append-only custom IDs.
- Images and labels match their approval hashes. No extra unapproved files are present.
- A source image or declared visit group cannot cross training/validation splits.
- Every enabled custom class has at least **35 train and 15 validation visits**.
- Generic classes are represented in validation for the stock regression check.

The visit minima follow the first acquisition target in the execution plan. They are
not a claim of statistical sufficiency or accuracy. The initial reviewed set is expected
to fail coverage; gather more independent examples rather than weaken review evidence.
The verifier ignores only the two known generated label-cache paths; the trainer removes
those caches before model loading so stale parsed labels cannot replace verified labels.
After evaluation, it checks the snapshot again before permitting an export.

Run `--baseline-only --base /local/models/yolo26n.pt` with the approved dataset first.
It evaluates without fine-tuning and writes a stock baseline bound to snapshot hash,
checkpoint hash and resolution. Baseline output must be a new file. A training run
requires that baseline (default `stock_baseline.json` beside the dataset, or `--baseline`).
Checkpoints/evaluation outputs go to local `--runs-dir`, not Dropbox.

The release gate requires metrics for every enabled custom class and every generic
validation class, applies custom accuracy minima and stock non-regression, and refuses
export on failure. The old `--force-export` bypass is removed. Dataset provenance comes
from the selected snapshot rather than the old default archive dataset. Resume support,
exported-NCNN evaluation and strengthened Pi deployment verification remain separate
execution-plan work. No inference-speed or production-accuracy claim follows from these
preflight tests alone.
