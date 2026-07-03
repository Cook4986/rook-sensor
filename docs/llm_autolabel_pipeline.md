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

The base 80-class model cannot distinguish an Amazon van from a box truck, or a baseball player from a pedestrian. The custom targets (already stubbed in `SCORE_MAP` as comments) are **fine-grained subclasses of classes YOLO already localizes well**:

```
truck / car / bus  →  trash_truck | ups_truck | fedex_truck | amazon_van | usps_truck
person             →  baseball_player
```

This subclass structure is what makes zero-manual-labeling possible.

---

## Pipeline Architecture

```
                         ┌────────────  Mac / workstation ("online" side)  ────────────┐
Pi (edge)                │                                                              │
────────                 │  Stage A          Stage B            Stage C        Stage D  │
unclassified/  ──sync──▶ │  Teacher YOLO ──▶ VLM crop        ──▶ YOLO dataset ─▶ Fine-  │
beast_cam/               │  (26l/x, low      classification      (COCO 80 +     tune +  │
                         │   conf) draws     (LLM assigns         custom IDs,   NCNN    │
                         │   the boxes       subclass labels      + background  export  │
                         │                   to crops)            negatives)            │
                         └──────────────────────────────────────────────┬───────────────┘
                                                                        │
Pi (edge)  ◀── deploy_model_to_pi.sh (versioned push, health check, rollback, Slack 🧠🔄)
```

### Division of labor: detector draws boxes, LLM names them

Vision LLMs are unreliable at emitting pixel-accurate coordinates but excellent at fine-grained classification of a cropped image ("is this truck a UPS truck, a FedEx truck, a trash truck, or none of these?"). So:

1. **Stage A — Teacher detection** (`llm_autolabel.py`): run YOLO26l/x at `conf=0.20` (the existing `reclassify_archive.py` setting) over the archive. Every box is a *localization proposal* with a COCO class.
2. **Stage B — VLM labeling** (`llm_autolabel.py`): crops of *refinable* classes (`truck`, `car`, `bus`, `person`) are sent to a vision LLM with a **closed-vocabulary prompt** (the six custom classes + `"none"`), structured JSON output, and a confidence gate. `"none"` keeps the original COCO label. Results are cached by image hash — each crop is billed once, ever.
3. **Stage C — Dataset assembly** (`llm_autolabel.py`): emits a standard YOLO dataset where class IDs are **COCO 0–79 preserved, custom classes appended as IDs 80–85**. Non-refined teacher boxes are written with their COCO IDs so the fine-tune sees local examples of `person`, `car`, `dog` etc. and does not catastrophically forget the base vocabulary. Frames from `processed/` are included as **empty-label background images** (hard negatives).
4. **Stage D — Train + export** (`train_custom_model.py`): transfer-learn from `yolo26n.pt` at `imgsz=1088` (deployment resolution), validate, **gate the release** on per-class mAP for custom classes *and* non-regression on base classes, then export NCNN and write a `model_card.json` manifest.
5. **Deploy** (`deploy_model_to_pi.sh`): versioned push to the Pi, atomic symlink swap, service restart, log health check, automatic rollback, and a Slack system-event notification.

### Quality controls (replacing the human annotator)

| Risk | Control |
|---|---|
| VLM mislabels a crop | Closed vocabulary + `"none"` escape hatch; per-label confidence threshold (default 0.8); labels below threshold fall back to the COCO class |
| VLM hallucinates JSON | Strict schema parse; malformed responses are retried once, then dropped (falls back to COCO label) |
| Teacher false positives become training data | Teacher conf 0.20 proposals are only *promoted* to custom labels by the VLM; un-refined proposals below 0.35 are discarded from the dataset |
| Base-class forgetting | Dataset keeps all 80 COCO IDs, includes local COCO-labeled boxes and background negatives; release gate checks base-class mAP against the previous model |
| Cost runaway | Content-hash cache (`autolabel_cache.jsonl`), `--max-crops` budget, crops resized to ≤512px before upload |
| Silent drift | `model_card.json` + Slack digest after every labeling/training run; dataset manifests are append-only and auditable |

### Human effort

None required per-image. The only recurring human touch points are (a) glancing at the Slack digest after a labeling run, and (b) approving deployment (running the deploy script). Both are review, not labeling.

---

## Conformance Review — UI / Notification System Spec

The detection update was checked against the three forward-looking specs. Findings and the resulting integration rules:

### 1. `docs/emoji_vocabulary.md` (notification vocabulary)

* **"One symbol per object class"** — each custom class gets a single dedicated symbol in `EMOJI_MAP`; composites remain reserved for anomalies/heuristics, consistent with the existing `🗑️🚚` linger composite:

| Emoji | Custom class | Livery cue |
| :---: | :--- | :--- |
| `🗑️` | `trash_truck` | Municipal sanitation |
| `📦` | `amazon_van` | Amazon blue Sprinter/Transit |
| `🟫` | `ups_truck` | UPS brown |
| `🟪` | `fedex_truck` | FedEx purple/orange |
| `📮` | `usps_truck` | USPS white LLV / ProMaster |
| `🧢` | `baseball_player` | Uniform + cap |

* **Scoring formula unchanged** — custom classes plug into the existing `score = Σ(base × count^1.5) + diversity` formula via `SCORE_MAP`; no new scoring path is introduced. Base scores use the values already stubbed in the code comments (`trash_truck` 15, delivery vehicles 12, `baseball_player` 8).
* **Alert thresholds respected** — delivery/trash detections reach the `≥ 30` Slack+Email tier through the existing heavy-vehicle bonus (extended to count custom truck subclasses); they do **not** bypass the score gate. A `baseball_game` congregation heuristic (3+ `baseball_player`) emits `⚾🏟️` and floors the score at 50 (the "rare/critical" tier), mirroring the existing crowd heuristics.
* **Silent solo classes unchanged** — no custom class is silent; a positively-identified delivery vehicle is exactly the "signal over noise" event Rook exists for.

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
LLM_MODEL=gpt-4o-mini          # any vision-capable chat model
LLM_MIN_CONFIDENCE=0.8         # below this, crop keeps its COCO label
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
