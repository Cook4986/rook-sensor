#!/usr/bin/env python3
"""
llm_autolabel.py — LLM-assisted auto-labeling of the Rook archive (Mac-side)

Turns the unclassified/reclassified archive into a YOLO training dataset with
ZERO manual annotation, using a two-model split:

  Stage A   Teacher detector (YOLO26l/x, conf=0.20) draws bounding boxes —
            detectors localize well; VLMs don't.
  Stage B1  A vision LLM classifies crops of refinable classes (vehicles,
            person, animals) into fine-grained local classes: per-vendor
            delivery vans (UPS/FedEx/Amazon/USPS/DHL), municipal vehicles,
            school buses, emergency responders, and specific wildlife
            (coyote, fox, deer, raccoon, raptor, ...) via a closed-vocabulary
            prompt with a "none" escape hatch — VLMs excel at crop
            classification.
  Stage B2  Whole-frame screening for frames where the teacher found NOTHING
            (the bulk of the unclassified archive): the VLM checks for
            wildlife the detector missed (squirrels, rabbits, turkeys...) and
            scene-level natural phenomena (downed tree, smoke/fire, flooding)
            and returns an approximate normalized bounding box. Boxes are
            rougher than detector boxes, so this stage uses a higher
            confidence gate and a minimum-area check; the target subjects
            (smoke plumes, fallen trees, prominent animals that survived the
            persistence gate) tolerate coarse localization.
  Stage C   Emits a standard YOLO dataset: COCO IDs 0-79 preserved, custom
            classes appended from ID 80, processed/ frames included as
            empty-label background negatives (anti-hallucination).

Privacy: runs entirely on the owner's Mac against the owner's own Dropbox
archive. Crops are sent to the configured LLM API only when LLM_API_KEY is
set; otherwise Stage B is skipped and the dataset uses teacher labels only.
Nothing here runs on (or transmits from) the Pi.

Cost control: every VLM verdict is cached by crop content hash in
autolabel_cache.jsonl — a crop is billed at most once, ever. Re-runs are
incremental and idempotent.

Spend priority order (added 2026-08-18): under --max-crops/--max-usd, frames
are processed beast_cam/ -> classified/ -> reclassified/ -> unclassified/ ->
processed/, NOT alphabetically and NOT by folder size. The first three are
guaranteed real content (a detection already exists); unclassified/ is the
zero-detection recall net (lower hit rate but the only source for subjects
too small to box); processed/ is verified-empty negatives we already hold
60x the needed count of, so it's funded last — a tight budget should be
spent on real content before re-confirming frames we know are empty.

Spend ledger (added 2026-08-18): --max-usd is enforced against a PERSISTENT
cross-process total in autolabel/spend_ledger.jsonl, not this run's own
in-memory counter. This closed a real gap — an orphaned/duplicate process
(or a kill that didn't reach a child) kept billing while a fresh run's own
zero-based counter had no way to see that spend, so two runs could each
individually respect a $50 cap while jointly blowing past it. Every
billable call appends its incremental cost immediately; the ledger is
append-only and summed at startup, same durability pattern as
autolabel_cache.jsonl.

Usage:
    python3 llm_autolabel.py                  # label everything new
    python3 llm_autolabel.py --dry-run        # report without writing dataset
    python3 llm_autolabel.py --max-crops 200  # cap LLM calls this run
    python3 llm_autolabel.py --max-usd 5.00   # stop once estimated spend hits $5
                                               # (requires LLM_PRICE_*_PER_1M below)
    python3 llm_autolabel.py --slack          # send Slack digest when done

Config (~/.env, Mac-side):
    LLM_API_BASE=https://api.openai.com/v1    # any OpenAI-compatible endpoint
    LLM_API_KEY=sk-...                        # unset → Stage B skipped
    LLM_MODEL=gpt-4o-mini                     # vision-capable chat model, both stages
    LLM_MODEL_B1=                             # override: Stage B1 crop classification
    LLM_MODEL_B2=                             # override: Stage B2 whole-frame screening
                                               # (both default to LLM_MODEL; split matters
                                               # because B2 is closer to detection than
                                               # classification, and newer "Flash"-tier
                                               # models may regress on it even as they
                                               # improve at B1)
    LLM_MIN_CONFIDENCE=0.8                    # below this → keep COCO label
    LLM_CROP_MAX_PX=384                       # longest-edge cap for Stage B1 crop uploads
    LLM_PRICE_INPUT_PER_1M=                   # USD per 1M input tokens — required for
    LLM_PRICE_OUTPUT_PER_1M=                  # USD per 1M output tokens — --max-usd to
                                               # actually enforce a cap; left unset (0) by
                                               # default rather than guessing a provider's
                                               # current rate. Check your provider's
                                               # pricing page and set both.

Gemini (via Google's OpenAI-compat endpoint):
    LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
    LLM_API_KEY=<Google AI Studio key>
    LLM_MODEL=gemini-3.5-flash
    LLM_REASONING_EFFORT=none                 # don't burn max_tokens on thinking
"""

import os
import re
import time
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
CLASSIFIED_DIR   = ARCHIVE_DIR / "classified"     # Pi-confirmed detections + .json sidecars
RECLASSIFIED_DIR = ARCHIVE_DIR / "reclassified"
PROCESSED_DIR    = ARCHIVE_DIR / "processed"      # hard negatives (verified empty)
BEAST_CAM_DIR    = ARCHIVE_DIR / "beast_cam"      # wildlife crops (date subdirs)
DATASET_DIR      = ARCHIVE_DIR / "autolabel"      # output YOLO dataset
CACHE_FILE       = DATASET_DIR / "autolabel_cache.jsonl"
# Persistent, cross-process spend ledger (added 2026-08-18, D-pending). A
# single run's --max-usd only sees its OWN in-memory cost_so_far, which is
# blind to spend from any other run — a crashed/orphaned process, a botched
# kill that leaves a child alive, or a duplicate launch. This file is the
# durable source of truth for "how much has this API key actually spent,
# ever" so --max-usd is a real hard ceiling across restarts, not per-run.
SPEND_LEDGER_FILE = DATASET_DIR / "spend_ledger.jsonl"

LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")
LLM_MODEL    = os.environ.get("LLM_MODEL", "gpt-4o-mini")
# Stage B1 (crop classification) and B2 (whole-frame screening) are different
# tasks — B2 is closer to detection, which newer "Flash"-tier models can
# regress on even as they improve at B1 — so they're independently overridable.
# Both default to LLM_MODEL: a config with only LLM_MODEL set is unaffected.
LLM_MODEL_B1 = os.environ.get("LLM_MODEL_B1", "") or LLM_MODEL
LLM_MODEL_B2 = os.environ.get("LLM_MODEL_B2", "") or LLM_MODEL
# Reasoning models (e.g. Gemini via the OpenAI-compat endpoint) spend "thinking"
# tokens against max_tokens; a 100-token budget can come back empty. Set to
# "none" (or "low") to suppress thinking for these single-JSON-verdict calls.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "")
LLM_MIN_CONF = float(os.environ.get("LLM_MIN_CONFIDENCE", "0.8"))
# Whole-frame verdicts carry VLM-drawn (approximate) boxes → stricter gate
SCENE_MIN_CONF = float(os.environ.get("LLM_SCENE_MIN_CONFIDENCE", "0.85"))
SCENE_MIN_BOX_AREA = 0.003   # normalized area floor — matches the archive's
                             # 800px² persistence gate at 640×360; rejects
                             # boxes too small for a rough VLM box to be useful

# Longest-edge cap (px) for Stage B1 crop uploads — livery/species ID doesn't
# need more detail than this, and it's the single biggest per-call cost lever.
LLM_CROP_MAX_PX = int(os.environ.get("LLM_CROP_MAX_PX", "384"))

# Cost estimation is opt-in: left at 0 (unconfigured) rather than hardcoding a
# provider's current rate, which this codebase has no way to verify is still
# accurate. Set both from your provider's pricing page to make --max-usd real.
LLM_PRICE_INPUT_PER_1M  = float(os.environ.get("LLM_PRICE_INPUT_PER_1M", "0") or "0")
LLM_PRICE_OUTPUT_PER_1M = float(os.environ.get("LLM_PRICE_OUTPUT_PER_1M", "0") or "0")

# Teacher proposals: same low threshold as reclassify_archive.py to catch
# faint/distant subjects; boxes NOT promoted by the VLM must clear a higher
# bar before entering the dataset (teacher false positives are poison).
TEACHER_CONF          = 0.20
TEACHER_KEEP_MIN_CONF = 0.35

# ── Custom vocabulary — the model contract ────────────────────────────────────
# Appended after COCO's 80 classes IN THIS ORDER (IDs 80+). rook_engine.py and
# train_custom_model.py must agree with this list. Grouped: vehicles, people,
# wildlife, natural phenomena.
CUSTOM_CLASSES = [
    # Vehicles — municipal / delivery / school / emergency (IDs 80-90)
    "trash_truck", "street_sweeper",
    "ups_truck", "fedex_truck", "amazon_van", "usps_truck", "dhl_van",
    "school_bus", "police_car", "fire_truck", "ambulance",
    # People (ID 91)
    "baseball_player",
    # Wildlife — specific local species (IDs 92-104)
    "coyote", "fox", "deer", "raccoon", "opossum", "skunk",
    "squirrel", "rabbit", "wild_turkey", "canada_goose",
    "raptor", "cardinal", "blue_jay",
    # Natural phenomena — scene-level events (IDs 105-107)
    "downed_tree", "smoke", "flood",
    # Curbside & service (IDs 108-109) — added Jul 2026. The list is
    # APPEND-ONLY: existing IDs are a contract with already-written datasets
    # and deployed models; new classes always go at the end.
    "trash_bins", "work_truck",
]

VISUAL_CUES = {
    # Vehicles
    "trash_truck":     "garbage/recycling truck: rear or side loader, hopper, municipal livery",
    "street_sweeper":  "street sweeper: compact municipal vehicle, rotating brushes, water spray",
    "ups_truck":       "UPS: brown package car or brown/gold semi, UPS shield logo",
    "fedex_truck":     "FedEx: white body with purple/orange or purple/green FedEx wordmark",
    "amazon_van":      "Amazon: blue-grey Sprinter/Transit van, Prime smile-arrow logo",
    "usps_truck":      "USPS mail van: white LLV/ProMaster, blue eagle logo, red-blue stripe",
    "dhl_van":         "DHL: yellow van with red DHL wordmark",
    "school_bus":      "school bus: yellow body, black lettering, flashing stop sign arm",
    "police_car":      "police: black-and-white or marked cruiser/SUV, light bar, shield decal",
    "fire_truck":      "fire apparatus: red engine/ladder truck, ladders, hose reels",
    "ambulance":       "ambulance: white/red box body, star of life, light bar",
    # People
    "baseball_player": "person in baseball uniform: cap, jersey, baseball pants, glove or bat",
    # Wildlife
    "coyote":          "coyote: lean grey-tan wild canid, pointed ears/muzzle, bushy drooping tail",
    "fox":             "fox: small canid, red-orange or grey coat, white-tipped bushy tail",
    "deer":            "deer: white-tailed deer, tan coat, slender legs; antlers if buck",
    "raccoon":         "raccoon: black face mask, ringed tail, grey stocky body",
    "opossum":         "opossum: grey-white fur, pointed white face, naked rat-like tail",
    "skunk":           "skunk: black body with bold white stripe(s), bushy tail",
    "squirrel":        "squirrel: small grey/brown rodent, prominent bushy tail",
    "rabbit":          "rabbit: cottontail, long ears, compact body, white tail puff",
    "wild_turkey":     "wild turkey: large dark ground bird, fan tail, red wattle",
    "canada_goose":    "Canada goose: black head/neck with white chinstrap, brown body",
    "raptor":          "bird of prey: hawk/owl/falcon — hooked beak, broad wings, perched or soaring",
    "cardinal":        "northern cardinal: vivid red songbird (male) or tan-red with crest (female)",
    "blue_jay":        "blue jay: blue crest and back, white/grey underside, black collar",
    # Natural phenomena
    "downed_tree":     "downed tree or large fallen branch across yard, path, or street",
    "smoke":           "smoke or visible fire: plume, haze column, or flames",
    "flood":           "standing or flowing floodwater covering ground/street surfaces",
    # Curbside & service
    "trash_bins":      "one or more wheeled curbside carts at the curb/driveway: "
                       "trash and/or recycling bins, upright, lids closed or open",
    "work_truck":      "work/contractor vehicle: pickup or van with roof or ladder "
                       "rack, utility body, toolboxes, equipment, or towing a trailer",
}

# COCO class → candidate custom subclasses the VLM may promote it to.
# Closed vocabulary per parent class keeps the LLM honest. Wildlife parents
# cover COCO's habitual confusions (raccoon→cat/bear, coyote→dog, deer→sheep).
REFINABLE = {
    # Vehicles
    "truck":  ["trash_truck", "street_sweeper", "ups_truck", "fedex_truck",
               "amazon_van", "usps_truck", "dhl_van", "fire_truck", "ambulance",
               "work_truck"],
    "bus":    ["trash_truck", "school_bus", "ups_truck", "fedex_truck",
               "amazon_van", "dhl_van", "fire_truck", "ambulance"],
    "car":    ["amazon_van", "usps_truck", "dhl_van", "police_car", "ambulance",
               "work_truck"],
    # People
    "person": ["baseball_player"],
    # Wildlife (COCO's generic/confused animal classes)
    "dog":    ["coyote", "fox", "deer"],
    "cat":    ["raccoon", "opossum", "skunk", "rabbit", "squirrel", "fox"],
    "bear":   ["raccoon", "deer"],
    "sheep":  ["deer", "coyote"],
    "cow":    ["deer"],
    "horse":  ["deer"],
    "bird":   ["raptor", "wild_turkey", "canada_goose", "cardinal", "blue_jay"],
}

# Whole-frame (Stage B2) vocabulary: subjects the teacher detector cannot
# propose — small wildlife it missed entirely, plus non-COCO scene phenomena.
# "flood" was removed after the Jul 2026 human review: all 22 flood finds were
# color-cast ground misreads (0/22 real). It stays in CUSTOM_CLASSES (the ID
# contract is untouched) — B2 just stops proposing it until the camera's
# white-balance cast is fixed. Every B2 find must pass the human review gate
# (review_custom_labels.py) before training: the same review measured B2
# wildlife precision at 1/30.
SCENE_CLASSES = ["coyote", "fox", "deer", "raccoon", "opossum", "skunk",
                 "squirrel", "rabbit", "wild_turkey", "canada_goose", "raptor",
                 "downed_tree", "smoke", "trash_bins"]

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


def _scene_prompt() -> str:
    cues = "\n".join(f"- {c}: {VISUAL_CUES[c]}" for c in SCENE_CLASSES)
    return (
        "This frame is from a fixed yard-monitoring camera. An object detector "
        "found nothing, but motion was observed. Check carefully for any of:\n"
        f"{cues}\n"
        "If exactly one is clearly present, answer with that label, your "
        "confidence, and an approximate bounding box [x_min, y_min, x_max, y_max] "
        "normalized to 0.0-1.0. If nothing matches, or you are unsure, answer "
        "'none'.\n"
        'Respond with ONLY this JSON: {"label": "<candidate-or-none>", '
        '"confidence": <0.0-1.0>, "box": [x_min, y_min, x_max, y_max]}'
    )


def _parse_scene_verdict(text: str):
    """Parse a whole-frame verdict; validate label vocabulary AND box geometry."""
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        label = str(data.get("label", "none")).strip().lower()
        conf = float(data.get("confidence", 0.0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if label == "none":
        return {"label": "none", "confidence": conf, "box": None}
    if label not in SCENE_CLASSES:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in data["box"])
    except (KeyError, ValueError, TypeError):
        return None
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(1.0, x2), min(1.0, y2)
    if x2 <= x1 or y2 <= y1 or (x2 - x1) * (y2 - y1) < SCENE_MIN_BOX_AREA:
        return None
    return {"label": label, "confidence": max(0.0, min(1.0, conf)),
            "box": [round(v, 4) for v in (x1, y1, x2, y2)]}


RATE_LIMIT_MAX_RETRIES = 5     # separate budget from ordinary-failure retries below
RATE_LIMIT_BASE_BACKOFF = 1.0  # seconds; doubles each retry, capped at 30s + jitter
NON_429_MAX_ATTEMPTS = 2       # unchanged from the original "one retry" behavior


def _vlm_round_trip(client: httpx.Client, jpg: bytes, prompt: str, parse_fn, model: str):
    """Shared VLM call. Returns (verdict, usage) — verdict is None on failure,
    usage is {"prompt_tokens", "completion_tokens"} (zeros if unavailable).

    Two independent retry budgets: 429s back off exponentially (honoring
    Retry-After when the provider sends one) and don't count against the
    ordinary-failure budget, since a rate limit isn't a broken request — it's
    the request working as intended, just needing to wait.
    """
    b64 = base64.b64encode(jpg).decode()
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 100,
        **({"reasoning_effort": LLM_REASONING_EFFORT} if LLM_REASONING_EFFORT else {}),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
            ],
        }],
    }
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    backoff = RATE_LIMIT_BASE_BACKOFF
    rate_limit_retries = 0
    attempts = 0
    while attempts < NON_429_MAX_ATTEMPTS:
        try:
            r = client.post(f"{LLM_API_BASE}/chat/completions", json=payload, timeout=60)
        except httpx.HTTPError:
            attempts += 1
            continue
        if r.status_code == 429:
            if rate_limit_retries >= RATE_LIMIT_MAX_RETRIES:
                break
            retry_after = r.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else backoff
            except ValueError:
                wait = backoff
            time.sleep(min(wait + random.uniform(0, 0.5), 30))
            backoff = min(backoff * 2, 16)
            rate_limit_retries += 1
            continue
        try:
            r.raise_for_status()
            body = r.json()
            u = body.get("usage") or {}
            usage["prompt_tokens"] = u.get("prompt_tokens", 0)
            usage["completion_tokens"] = u.get("completion_tokens", 0)
            verdict = parse_fn(body["choices"][0]["message"]["content"])
            if verdict is not None:
                return verdict, usage
        except httpx.HTTPError:
            pass
        attempts += 1
    return None, usage


def classify_frame(client: httpx.Client, frame_jpg: bytes):
    """Stage B2: whole-frame screening. Returns ({"label","confidence","box"} or None, usage)."""
    return _vlm_round_trip(client, frame_jpg, _scene_prompt(), _parse_scene_verdict, LLM_MODEL_B2)


def classify_crop(client: httpx.Client, crop_jpg: bytes, parent_class: str, candidates: list):
    """Stage B1: one crop-classification round-trip. Returns ({"label","confidence"} or
    None, usage) — malformed responses are retried once, then dropped (caller falls
    back to the COCO label); a bad label is worse than no label.
    """
    return _vlm_round_trip(client, crop_jpg, _crop_prompt(parent_class, candidates),
                           lambda text: _parse_verdict(text, candidates), LLM_MODEL_B1)


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


def load_ledger_total() -> float:
    """Sum of every recorded spend increment, ever, across all runs and
    processes. Append-only, same durability pattern as the label cache —
    a crash mid-write loses at most one partial line, never prior totals."""
    total = 0.0
    if SPEND_LEDGER_FILE.exists():
        with open(SPEND_LEDGER_FILE) as f:
            for line in f:
                try:
                    total += json.loads(line)["delta_usd"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    return total


def append_ledger(delta_usd: float, note: str = ""):
    """Record one incremental spend event immediately — called right after
    each billable call, not batched, so a killed/orphaned process's spend is
    visible to every other process's next ledger read, not just its own
    in-memory counter."""
    if delta_usd <= 0:
        return
    SPEND_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SPEND_LEDGER_FILE, "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                             "delta_usd": round(delta_usd, 6), "note": note}) + "\n")


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
    """dataset.yaml: COCO 0-79 preserved, custom classes appended from ID 80."""
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
    parser.add_argument("--max-usd", type=float, default=None,
                        help="stop new LLM calls once TOTAL estimated spend — this run "
                             "PLUS every prior run's recorded spend in "
                             "autolabel/spend_ledger.jsonl — hits this many dollars "
                             "(requires LLM_PRICE_INPUT_PER_1M/LLM_PRICE_OUTPUT_PER_1M "
                             "to be set — otherwise a warning is printed and the cap "
                             "is NOT enforced)")
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

    usd_priced = LLM_PRICE_INPUT_PER_1M > 0 or LLM_PRICE_OUTPUT_PER_1M > 0
    usd_enforced = args.max_usd is not None and usd_priced
    if args.max_usd is not None and not usd_priced:
        print(f"⚠️  --max-usd {args.max_usd:.4f} set but LLM_PRICE_INPUT_PER_1M/"
              f"LLM_PRICE_OUTPUT_PER_1M are both unset (0) — cost cannot be estimated, "
              f"so this run will NOT stop on spend. Set both from your provider's "
              f"current pricing page to enforce it.")

    vlm_label = LLM_MODEL if LLM_MODEL_B1 == LLM_MODEL_B2 else f"B1={LLM_MODEL_B1} B2={LLM_MODEL_B2}"
    print(f"🏷️  Rook Auto-Label — teacher yolo26{args.model}.pt, VLM {vlm_label}")
    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        device = "cpu"
    teacher = YOLO(f"yolo26{args.model}.pt")
    coco_names = teacher.names
    name_to_id = {v: k for k, v in coco_names.items()}
    custom_id = {name: 80 + i for i, name in enumerate(CUSTOM_CLASSES)}

    # Sources, in SPEND PRIORITY ORDER (matters under --max-usd/--max-crops —
    # once a cap hits, everything after it in this list gets skipped, so
    # guaranteed-content folders must come before speculative ones):
    #   beast_cam/     wildlife crops — ideal Stage B1 species inputs; smallest
    #                  folder, directly targets the weakest class category
    #   classified/    Pi-confirmed detections at conf 0.70 — hard positives that
    #                  REFINE existing classes, plus confirmed truck/bird parents
    #                  to mine vendor/species subclasses from (.json sidecars
    #                  carry the Pi's verdict for agreement auditing)
    #   reclassified/  Mac-teacher finds the Pi missed — also guaranteed real
    #                  content (a box already exists)
    #   unclassified/  ghost motion — subjects the Pi missed (recall examples).
    #                  Zero-detection by definition, so Stage B2 only; the sole
    #                  source for wildlife/scene subjects too small to box, but
    #                  historically <1% non-"none" verdicts (D2/D13 quality bugs)
    #   processed/     teacher-empty frames already verified empty — screened by
    #                  Stage B2 for the same recall reasons as unclassified/, but
    #                  LAST: we already carry 18k+ negatives against a ~300
    #                  target, so re-screening them is the lowest-value spend in
    #                  the archive. Frames that stay empty remain the
    #                  background-negatives pool regardless of scan order.
    def _priority_frames():
        seen = set()
        ordered = []
        for d, pattern in ((BEAST_CAM_DIR, "*/*.jpg"), (CLASSIFIED_DIR, "*.jpg"),
                           (RECLASSIFIED_DIR, "*.jpg"), (UNCLASSIFIED_DIR, "*.jpg"),
                           (PROCESSED_DIR, "*.jpg")):
            for p in sorted(d.glob(pattern)):
                if p not in seen:
                    seen.add(p)
                    ordered.append(p)
        return ordered
    frames = _priority_frames()
    if not frames:
        print("   No archive frames to process.")
        return

    cache = load_cache()
    client = httpx.Client(headers={"Authorization": f"Bearer {LLM_API_KEY}"})

    prior_spend = load_ledger_total() if usd_priced else 0.0
    if usd_enforced and prior_spend > 0:
        print(f"   💳 Spend ledger: ${prior_spend:.4f} already spent across prior/other "
              f"runs — --max-usd {args.max_usd:.4f} is a TOTAL ceiling, not per-run.")

    llm_calls = 0
    tokens_in = 0
    tokens_out = 0
    cost_so_far = 0.0
    budget_msg_printed = False
    stats = {"frames": 0, "boxes": 0, "promoted": {}, "scene_finds": {},
             "cache_hits": 0, "llm_failures": 0,
             "classified_frames": 0, "pi_agree": 0, "pi_disagree": 0}

    def budget_left() -> bool:
        nonlocal budget_msg_printed
        if llm_calls >= args.max_crops:
            if not budget_msg_printed:
                print(f"   💰 --max-crops {args.max_crops} reached — no further LLM calls this run.")
                budget_msg_printed = True
            return False
        if usd_enforced and (prior_spend + cost_so_far) >= args.max_usd:
            if not budget_msg_printed:
                print(f"   💰 --max-usd {args.max_usd:.4f} reached (this run: ${cost_so_far:.4f} + "
                      f"prior: ${prior_spend:.4f} = ${prior_spend + cost_so_far:.4f}) — "
                      f"no further LLM calls this run.")
                budget_msg_printed = True
            return False
        return True

    print(f"   Processing {len(frames)} frames (cache: {len(cache)} verdicts)...\n")

    skipped_unreadable = 0
    labeled_names = set()
    progress_start = time.monotonic()
    for idx, img_path in enumerate(frames):
        if (idx + 1) % 500 == 0:
            elapsed = time.monotonic() - progress_start
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta_s = (len(frames) - (idx + 1)) / rate if rate > 0 else 0
            cost_str = (f"${cost_so_far:.4f} this run (${prior_spend + cost_so_far:.4f} total)"
                        if usd_priced else "unpriced")
            print(f"   … {idx + 1}/{len(frames)} frames ({rate:.1f}/s, "
                  f"ETA {eta_s/60:.0f}m) — {llm_calls} LLM calls, {cost_str}", flush=True)
        # Dropbox sync can leave zero-byte/partial JPEGs — skip, don't crash
        frame = cv2.imread(str(img_path))
        if frame is None or frame.size == 0:
            skipped_unreadable += 1
            print(f"   ⚠️  Skipping unreadable frame: {img_path.name}")
            continue
        results = teacher(frame, conf=TEACHER_CONF, device=device, verbose=False)
        boxes = results[0].boxes
        img_h, img_w = results[0].orig_shape
        labels = []
        is_classified = img_path.parent.name == "classified"

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
                    # Cap upload size — 384px longest edge is plenty for livery ID
                    scale = LLM_CROP_MAX_PX / max(crop.shape[:2])
                    if scale < 1.0:
                        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)),
                                                 max(1, int(crop.shape[0] * scale))))
                    ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    crop_hash = hashlib.sha256(jpg.tobytes()).hexdigest() if ok else None

                    verdict = None
                    if crop_hash and crop_hash in cache:
                        verdict = cache[crop_hash]
                        stats["cache_hits"] += 1
                    elif crop_hash and budget_left():
                        verdict, usage = classify_crop(client, jpg.tobytes(), cls_name,
                                                       REFINABLE[cls_name])
                        llm_calls += 1
                        tokens_in += usage["prompt_tokens"]
                        tokens_out += usage["completion_tokens"]
                        call_cost = (usage["prompt_tokens"] / 1e6) * LLM_PRICE_INPUT_PER_1M \
                                  + (usage["completion_tokens"] / 1e6) * LLM_PRICE_OUTPUT_PER_1M
                        cost_so_far += call_cost
                        if usd_priced:
                            append_ledger(call_cost, note=f"B1 {cls_name}")
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

        # ── Stage B2: whole-frame screening when the teacher found nothing ──
        # These are the frames the pipeline exists for — motion with no COCO
        # detection: small wildlife (squirrels, rabbits, turkeys) and scene
        # phenomena (downed trees, smoke, flooding) the detector cannot propose.
        if not labels and LLM_API_KEY:
            frame_hash = hashlib.sha256(img_path.read_bytes()).hexdigest()
            verdict = None
            if frame_hash in cache:
                verdict = cache[frame_hash]
                stats["cache_hits"] += 1
            elif budget_left():
                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    verdict, usage = classify_frame(client, jpg.tobytes())
                    llm_calls += 1
                    tokens_in += usage["prompt_tokens"]
                    tokens_out += usage["completion_tokens"]
                    call_cost = (usage["prompt_tokens"] / 1e6) * LLM_PRICE_INPUT_PER_1M \
                              + (usage["completion_tokens"] / 1e6) * LLM_PRICE_OUTPUT_PER_1M
                    cost_so_far += call_cost
                    if usd_priced:
                        append_ledger(call_cost, note="B2 frame")
                    if verdict is None:
                        stats["llm_failures"] += 1
                    else:
                        append_cache({"hash": frame_hash, "parent": "__frame__",
                                      "source": img_path.name, **verdict})
                        cache[frame_hash] = verdict

            if (verdict and verdict["label"] != "none" and verdict.get("box")
                    and verdict["confidence"] >= SCENE_MIN_CONF):
                bx1, by1, bx2, by2 = verdict["box"]
                labels.append((custom_id[verdict["label"]],
                               (bx1 + bx2) / 2, (by1 + by2) / 2,
                               bx2 - bx1, by2 - by1))
                stats["boxes"] += 1
                stats["scene_finds"][verdict["label"]] = \
                    stats["scene_finds"].get(verdict["label"], 0) + 1

        # ── Pi-vs-teacher agreement audit (classified/ frames only) ────────
        # The sidecar records what the Pi detected at conf 0.70. Comparing it
        # against the teacher's verdict quantifies where the deployed nano
        # model needs refinement — persistent disagreement per class is the
        # signal to grow that class's share of the dataset.
        if is_classified:
            stats["classified_frames"] += 1
            sidecar = img_path.with_suffix(".json")
            if sidecar.exists():
                try:
                    pi_classes = set(json.loads(sidecar.read_text()).get("pi_classes", []))
                    teacher_classes = {coco_names[int(c)] for i, c in enumerate(boxes.cls)
                                       if float(boxes.conf[i]) >= TEACHER_KEEP_MIN_CONF}
                    if pi_classes <= teacher_classes:
                        stats["pi_agree"] += 1
                    else:
                        stats["pi_disagree"] += 1
                except (json.JSONDecodeError, OSError):
                    pass

        if labels and not args.dry_run:
            write_sample(img_path, labels, split_of(img_path))
        if labels:
            stats["frames"] += 1
            labeled_names.add(img_path.name)

    # ── Background negatives: verified-empty frames teach "nothing here" ─────
    # Exclude frames Stage B2 just labeled — writing them as empty negatives
    # would overwrite the labeled sample (same filename) and poison training.
    negatives = sorted(p for p in PROCESSED_DIR.glob("*.jpg")
                       if p.name not in labeled_names)
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
            "vlm_b1": LLM_MODEL_B1 if LLM_API_KEY else None,
            "vlm_b2": LLM_MODEL_B2 if LLM_API_KEY else None,
            "vlm_min_confidence": LLM_MIN_CONF,
            "scene_min_confidence": SCENE_MIN_CONF,
            "crop_max_px": LLM_CROP_MAX_PX,
            "labeled_frames": stats["frames"], "boxes": stats["boxes"],
            "negatives": len(negatives), "promoted": stats["promoted"],
            "scene_finds": stats["scene_finds"],
            "classified_frames": stats["classified_frames"],
            "pi_teacher_agreement": {"agree": stats["pi_agree"],
                                     "disagree": stats["pi_disagree"]},
            "llm_calls": llm_calls, "cache_hits": stats["cache_hits"],
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "estimated_cost_usd": round(cost_so_far, 4) if usd_priced else None,
            "prior_ledger_spend_usd": round(prior_spend, 4) if usd_priced else None,
            "total_spend_to_date_usd": round(prior_spend + cost_so_far, 4) if usd_priced else None,
            "max_usd_cap": args.max_usd if usd_enforced else None,
        }
        with open(DATASET_DIR / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

    # ── Summary ───────────────────────────────────────────────────────────────
    promoted_total = sum(stats["promoted"].values())
    scene_total = sum(stats["scene_finds"].values())
    print(f"\n{'=' * 48}")
    print(f"🏷️  Rook Auto-Label Complete{' (dry run)' if args.dry_run else ''}")
    print(f"   Frames labeled    : {stats['frames']}")
    print(f"   Boxes written     : {stats['boxes']}")
    print(f"   Custom promotions : {promoted_total}")
    for name, n in sorted(stats["promoted"].items(), key=lambda x: -x[1]):
        print(f"      {name}: {n}")
    print(f"   Scene finds (B2)  : {scene_total}")
    for name, n in sorted(stats["scene_finds"].items(), key=lambda x: -x[1]):
        print(f"      {name}: {n}")
    if stats["classified_frames"]:
        audited = stats["pi_agree"] + stats["pi_disagree"]
        print(f"   Classified frames : {stats['classified_frames']} "
              f"(Pi↔teacher agreement: {stats['pi_agree']}/{audited})")
    print(f"   Background negs   : {len(negatives)}")
    print(f"   LLM calls / cache : {llm_calls} / {stats['cache_hits']}")
    if tokens_in or tokens_out:
        cost_str = (f"est. ${cost_so_far:.4f} this run, ${prior_spend + cost_so_far:.4f} total to date"
                    if usd_priced else "cost unpriced (set LLM_PRICE_*_PER_1M)")
        print(f"   Tokens in / out   : {tokens_in} / {tokens_out} ({cost_str})")
    if stats["llm_failures"]:
        print(f"   LLM failures      : {stats['llm_failures']} (kept COCO labels)")
    if skipped_unreadable:
        print(f"   Unreadable frames : {skipped_unreadable} (skipped)")
    if not args.dry_run:
        print(f"   Dataset           : {DATASET_DIR}/dataset.yaml")
    print(f"{'=' * 48}\n")

    if args.slack and not args.dry_run:
        combined = dict(stats["promoted"])
        for k, v in stats["scene_finds"].items():
            combined[k] = combined.get(k, 0) + v
        summary = "  ".join(f"{k} ×{v}" for k, v in
                            sorted(combined.items(), key=lambda x: -x[1]))
        cost_note = f", est. ${cost_so_far:.4f} (${prior_spend + cost_so_far:.4f} total)" if usd_priced else ""
        send_slack(f"🏷️ *Rook Auto-Label* — {stats['frames']} frames → "
                   f"{stats['boxes']} boxes ({promoted_total} crop promotions, "
                   f"{scene_total} whole-frame finds)\n"
                   f"   {summary or 'no custom classes found'}\n"
                   f"   LLM calls: {llm_calls} (cache hits: {stats['cache_hits']}{cost_note}) — "
                   f"dataset at `archive/autolabel/`")
        print("💬 Slack digest sent.")


if __name__ == "__main__":
    main()
