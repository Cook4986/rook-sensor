# LLM Auto-Label Pipeline — Extending YOLO with Local Imagery, Zero Manual Annotation

This document supersedes the annotation step (§2) of [`rook_custom_model_proposal.md`](../rook_custom_model_proposal.md). It describes how Rook leverages the **unclassified image archive** to extend and refine the on-device YOLO model using **LLM-based online labeling** — no Roboflow, no CVAT, no human-drawn boxes — and how the resulting detection update conforms to the notification/UI specs (`docs/emoji_vocabulary.md`, `docs/refinements.md` v2 dashboard, `PRIVACY.md`).

---

## Why the Unclassified Archive Is the Right Dataset

Rook already produces a continuously-growing, pre-filtered corpus of **local imagery from the exact deployment viewpoint**:

| Source | Content | Training value |
|---|---|---|
| `archive/unclassified/` | Motion that passed the persistence gate but YOLO26n found nothing | Subjects the nano model **misses** — the highest-value recall examples |
| `archive/reclassified/` | Frames where the Mac-side YOLO26l/x pass found objects the Pi missed | Pre-confirmed hard positives with teacher detections |
| `archive/processed/` | Frames verified to contain nothing | **Hard negatives** — background images that teach the model to stop hallucinating the houselight-as-traffic-light class of errors |
| `beast_cam/` | Wildlife bounding-box crops | Fine-grained species/subject examples |

Because every frame comes from the deployed camera (same lens, same angle, same lighting cycle), a model fine-tuned on this corpus is refined *for this scene specifically* — the definition of local refinement.

## Why COCO Cannot Deliver the Target Classes

The base 80-class model cannot distinguish an Amazon van from a box truck, a coyote from a dog, or a baseball player from a pedestrian — and it has no class at all for scene-level natural phenomena like a downed tree or smoke. The custom vocabulary (28 classes, IDs 80–107) covers three structural cases:

**1. Fine-grained subclasses of classes YOLO already localizes well** — vehicles by vendor/function and people by role:

```
truck / car / bus  →  trash_truck | street_sweeper | ups_truck | fedex_truck |
                      amazon_van | usps_truck | dhl_van | school_bus |
                      police_car | fire_truck | ambulance
person             →  baseball_player
```

**2. Specific wildlife hiding inside COCO's generic/confused animal classes** — COCO habitually reads raccoons as cats or bears, coyotes as dogs, and deer as sheep; the engine currently papers over this with heuristics (solo-dog-at-quiet-hours ≈ coyote, sheep/cow remapped to 🦌, HSV color ≈ cardinal/blue jay):

```
dog / sheep / cow / horse  →  coyote | fox | deer
cat / bear                 →  raccoon | opossum | skunk | rabbit | squirrel | fox
bird                       →  raptor | wild_turkey | canada_goose | cardinal | blue_jay
```

**3. Subjects the detector cannot propose at all** — small wildlife it misses outright (squirrels, rabbits) and non-object natural phenomena. These live in exactly the frames the unclassified archive collects (motion with zero detections) and are handled by whole-frame screening (Stage B2 below):

```
(no COCO parent)  →  squirrel | rabbit | wild_turkey | ...  (missed wildlife)
                     downed_tree | smoke | flood             (scene phenomena)
```

Cases 1–2 are what make zero-manual-labeling straightforward (detector boxes + LLM crop classification); case 3 requires the LLM to also localize, which is acceptable because these subjects are large or prominence-gated (see Stage B2 quality controls).

---

## Pipeline Architecture

```
                         ┌────────────  Mac / workstation ("online" side)  ─────────────┐
Pi (edge)                │                                                               │
────────                 │  Stage A          Stage B1            Stage C        Stage D  │
unclassified/  ──sync──▶ │  Teacher YOLO ──▶ VLM crop         ──▶ YOLO dataset ─▶ Fine-  │
beast_cam/               │  (26l/x, low      classification       (COCO 80 +     tune +  │
                         │   conf) draws     (vendor vehicles,     custom IDs,   NCNN    │
                         │   the boxes       specific wildlife)    + background  export  │
                         │        │                                negatives)            │
                         │        └─ no detections? ─▶ Stage B2                          │
                         │           whole-frame VLM screening                           │
                         │           (missed wildlife + downed_tree/smoke/flood,         │
                         │            approximate box, stricter gate)                    │
                         └───────────────────────────────────────────────┬───────────────┘
                                                                         │
Pi (edge)  ◀── deploy_model_to_pi.sh (versioned push, health check, rollback, Slack 🧠🔄)
```

### Division of labor: detector draws boxes, LLM names them

Vision LLMs are unreliable at emitting pixel-accurate coordinates but excellent at fine-grained classification of a cropped image ("is this truck a UPS truck, a FedEx truck, a trash truck, or none of these?"). So:

1. **Stage A — Teacher detection** (`llm_autolabel.py`): run YOLO26l/x at `conf=0.20` (the existing `reclassify_archive.py` setting) over the archive — `unclassified/`, `reclassified/`, and Beast Cam wildlife crops. Every box is a *localization proposal* with a COCO class.
2. **Stage B1 — VLM crop labeling** (`llm_autolabel.py`): crops of *refinable* classes (vehicles, `person`, and the generic animal classes `dog`/`cat`/`bear`/`sheep`/`cow`/`horse`/`bird`) are sent to a vision LLM with a **closed-vocabulary prompt** (the candidate subclasses for that parent + `"none"`), structured JSON output, and a confidence gate. `"none"` keeps the original COCO label. Results are cached by image hash — each crop is billed once, ever.
3. **Stage B2 — Whole-frame screening** (`llm_autolabel.py`): frames where the teacher found *nothing* — the bulk of the unclassified archive — get one whole-frame VLM pass against the scene vocabulary (missed wildlife + `downed_tree`/`smoke`/`flood`). The VLM returns a label **and an approximate normalized bounding box**. Because VLM boxes are rough, this stage carries extra controls: a stricter confidence gate (`LLM_SCENE_MIN_CONFIDENCE`, default 0.85 vs 0.8), box geometry validation, and a minimum-area floor (0.3% of frame, matching the archive's 800px² persistence gate) — and the target subjects tolerate coarse boxes by nature (smoke plumes and downed trees are large; animals only enter the archive if prominent enough to pass the persistence gate).
4. **Stage C — Dataset assembly** (`llm_autolabel.py`): emits a standard YOLO dataset where class IDs are **COCO 0–79 preserved, custom classes appended as IDs 80–107**. Non-refined teacher boxes are written with their COCO IDs so the fine-tune sees local examples of `person`, `car`, `dog` etc. and does not catastrophically forget the base vocabulary. Frames from `processed/` are included as **empty-label background images** (hard negatives).
5. **Stage D — Train + export** (`train_custom_model.py`): transfer-learn from `yolo26n.pt` at `imgsz=1088` (deployment resolution), validate, **gate the release** on per-class mAP for custom classes *and* non-regression on base classes, then export NCNN and write a `model_card.json` manifest.
6. **Deploy** (`deploy_model_to_pi.sh`): versioned push to the Pi, atomic symlink swap, service restart, log health check, automatic rollback, and a Slack system-event notification.

### Quality controls (replacing the human annotator)

| Risk | Control |
|---|---|
| VLM mislabels a crop | Closed vocabulary + `"none"` escape hatch; per-label confidence threshold (default 0.8); labels below threshold fall back to the COCO class |
| VLM hallucinates JSON | Strict schema parse; malformed responses are retried once, then dropped (falls back to COCO label) |
| VLM boxes are imprecise (Stage B2) | Stricter 0.85 confidence gate; box geometry validated and clamped; minimum-area floor rejects boxes too small for a rough box to be useful training signal |
| Teacher false positives become training data | Teacher conf 0.20 proposals are only *promoted* to custom labels by the VLM; un-refined proposals below 0.35 are discarded from the dataset |
| Base-class forgetting | Dataset keeps all 80 COCO IDs, includes local COCO-labeled boxes and background negatives; release gate checks base-class mAP against the previous model |
| Cost runaway | Content-hash cache (`autolabel_cache.jsonl`) covers crops *and* whole frames, shared `--max-crops` budget, uploads resized to ≤512px |
| Silent drift | `model_card.json` + Slack digest after every labeling/training run; dataset manifests are append-only and auditable |

### Human effort

None required per-image. The only recurring human touch points are (a) glancing at the Slack digest after a labeling run, and (b) approving deployment (running the deploy script). Both are review, not labeling.

---

## Conformance Review — UI / Notification System Spec

The detection update was checked against the three forward-looking specs. Findings and the resulting integration rules:

### 1. `docs/emoji_vocabulary.md` (notification vocabulary)

* **"One symbol per object class"** — each custom class gets a single dedicated symbol in `EMOJI_MAP`; composites remain reserved for anomalies/critical events, consistent with the existing `🗑️🚚` linger composite. The full symbol/score tables live in `emoji_vocabulary.md` (custom vehicle, custom wildlife, and natural phenomena sections). Emergency responders (`police_car`/`fire_truck`/`ambulance`) render as the vocabulary's established `🚨🚓`-style composites; phenomena use anomaly composites (`🌳⚠️`, `🔥💨`).
* **Heuristics superseded, not duplicated** — confirmed species replace the COCO-era proxies when the custom model is live: `coyote` supersedes the solo-dog-at-quiet-hours guess, `deer` the sheep/cow remap, `cardinal`/`blue_jay` the HSV color heuristic, `raptor` the solo-bird assumption. The old heuristics remain in the engine and still cover anything the custom model doesn't catch (class names differ, so both paths coexist without conflict).
* **Scoring formula unchanged** — custom classes plug into the existing `score = Σ(base × count^1.5) + diversity` formula via `SCORE_MAP`; no new scoring path is introduced. Delivery/municipal vehicles use the values stubbed in the original code comments (`trash_truck` 15, delivery 12); wildlife scores are calibrated against existing precedent (`deer` 50 matches the sheep/cow remap, `raptor` 20 above generic `bird` 10, `smoke` 100 at the bear tier).
* **Alert thresholds respected** — delivery/municipal detections reach the `≥ 30` Slack+Email tier through the existing heavy-vehicle bonus (extended to count custom truck subclasses); they do **not** bypass the score gate. Emergency responders and the `baseball_game` congregation heuristic (3+ `baseball_player` → `⚾🏟️`) floor at 50, the "rare/critical" tier, mirroring the existing crowd heuristics.
* **Silent solo classes extended in the spirit of the spec** — `squirrel` and `rabbit` join `car`/`bicycle`/`horse` as silent-solo: ubiquitous yard wildlife is counted in daily stats and still cached by Beast Cam, but never worth a real-time alert alone. Every other custom class alerts — a positively-identified delivery van or coyote is exactly the "signal over noise" event Rook exists for.

### 2. `docs/refinements.md` (v2 roadmap: dashboard, lingering)

* **Truck lingering gap closed as designed** — `LINGER_THRESHOLDS` deliberately omits `truck` ("trash/delivery trucks operate on a 2–5 min cycle... Add to custom training plan for proper detection"). The custom model resolves this the way the note intended: `trash_truck` and delivery classes alert **on detection** (score path), so no lingering threshold is needed and the 2–5 min cycle is no longer a blind spot.
* **v2 web dashboard (Next.js + Supabase + Vercel)** — the pipeline emits dashboard-ready artifacts so the future UI can surface model provenance without rework:
  - `model_card.json` per trained model: version, dataset counts per class, mAP per class, base-class regression check, export settings. This maps 1:1 onto a future Supabase `model_versions` table.
  - The engine logs the loaded model label (and custom-vocabulary status) at startup; the dashboard's planned "threshold tuning" page gains a "model version" facet for free.
  - Deploy notifications use the established system-event emoji style (`🧠🔄 Model updated…`), consistent with `💚 engine started` and `🌡️` thermal events.
* **Engine stays model-agnostic** — the engine inspects `model.names` at load time and simply logs whether the extended vocabulary is active. All custom-class maps are inert when the base 80-class model is loaded, so script deploys (`deploy_to_pi.sh`) and model deploys (`deploy_model_to_pi.sh`) remain independent, matching the current deployment split.

### 3. `PRIVACY.md` / `TERMS.md`

* **New third-party data flow disclosed** — VLM labeling sends **owner-initiated, Mac-side crops from the owner's own archive** to a configured LLM API. This is a new row in the PRIVACY.md third-party table. It is opt-in (no API key configured → Stage B is skipped and the pipeline degrades to teacher-only labels), runs only when the owner invokes the tool, and never touches the Pi's real-time path.
* **Edge privacy posture unchanged** — no cloud inference is added to the device; frames still exist only in RAM during live operation; the archive flow (Pi → owner's Mac → owner's Dropbox) is unchanged. The LLM step consumes the archive the owner already possesses.
* **SMS compliance untouched** — no changes to message types beyond emoji vocabulary already covered by "emoji-based yard-activity alerts" in `docs/SMS_COMPLIANCE.md`.

---

## Tooling Reference

| Tool | Runs on | Purpose |
|---|---|---|
| `app/llm_autolabel.py` | Mac | Stages A–C: teacher detection → VLM crop labeling → YOLO dataset under `archive/autolabel/` |
| `app/train_custom_model.py` | Mac / GPU box | Stage D: fine-tune, validation gate, NCNN export, `model_card.json` |
| `app/deploy_model_to_pi.sh` | Mac | Versioned model push, symlink swap, health check, rollback, Slack notice |

### Configuration (`~/.env`, Mac-side — never on the Pi)

```bash
# OpenAI-compatible endpoint (OpenAI, OpenRouter, or local Ollama/vLLM)
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini              # any vision-capable chat model
LLM_MIN_CONFIDENCE=0.8             # Stage B1: below this, crop keeps its COCO label
LLM_SCENE_MIN_CONFIDENCE=0.85      # Stage B2: stricter — VLM boxes are approximate
```

### Typical cycle

```bash
# 1. Label everything new in the archive (idempotent, cached)
python3 app/llm_autolabel.py --slack

# 2. Train + gate + export (refuses to export on regression)
python3 app/train_custom_model.py

# 3. Push to the Pi (auto-rollback on unhealthy restart)
bash app/deploy_model_to_pi.sh
```

Each run is incremental: the cache means only never-before-seen crops hit the LLM, so the cycle can run weekly (or from a launchd timer next to the sync agent) as the archive grows.
