# Rook v2: Refinements & Known Issues

Identified hardware and software improvements from prototyping, assembly, and live testing (May 2026).

---

## Hardware

### 1. Power Supply — **Resolved in v1.1**
- **Issue:** USB-A QC 3.0 (15W) + 10ft flat cable caused brownouts when YOLO inference spiked CPU to 100%, triggering a PMIC halt (solid red LED).
- **Resolution:** Upgrade to a **5V/5A USB-C PD** charger + flat 100W cable with 5A E-Marker chip. Standard 3A chargers are insufficient.

### 2. Arducam B0444 Mounting
- **Issue:** The B0444 PCB is 24×25mm with no standard mounting holes. Cannot use M2/M2.5 standoffs.
- **Resolution:** Custom PETG enclosure required. Camera seats in a friction-fit aperture channel (14mm lens barrel clearance, 26×27mm retention channel). See `docs/enclosure_spec.md`.

### 3. Raspberry Pi 5 Standoffs
- **Issue:** Pi 5 requires M2.5 screws/standoffs for its 85×56mm mounting pattern — not included in original BOM.
- **Resolution:** Add M2.5 standoff kit to BOM. Required for secure enclosure assembly.

### 4. Thermal Management
- **Issue:** YOLO inference on Cortex-A76 causes rapid heat spikes. Observed 67°C under sustained inference, 53°C idle. Hard shutdown at 80°C.
- **Mitigations applied:**
  - Thermal reads rate-limited to every 30s (not per-frame)
  - Inference runs in the main loop; alerts dispatch async to avoid CPU spikes from blocking SMTP/Slack
- **v2 Resolution:** Hailo-8L AI Accelerator HAT+ offloads inference entirely, reducing SoC thermal load to near-idle.

---

## Software

### 1. Exposure Scheduling
- **Current:** Day/night exposure set by `suntime` sunrise/sunset tables, re-evaluated every 10 minutes.
- **Limitation:** Doesn't account for heavy overcast or bright artificial lighting.
- **v2:** Poll camera `AnalogueGain` metadata to dynamically adjust `ExposureValue` independent of the motion loop.

### 2. Wildlife Species Resolution
- **Current:** COCO classifies all wildlife into generic classes (`bird`, `dog`, `sheep`). Rook adds a local species context hint from the iNat Observations API at startup (e.g. `🦅 (locally: Red-tailed Hawk, Canada Goose)`).
- **Limitation:** Hint is a lookup, not inference — all birds get the same hint list regardless of what's in frame.
- **v2 (post-Hailo):** Run EfficientNet-B0 on Beast Cam crops nightly for species-level ID in the daily digest.

### 3. Beast Cam
- **Current:** YOLO-detected wildlife bounding boxes are cropped and cached to `~/beast_cam/YYYY-MM-DD/` in real-time (async, no inference cost). Attached to 3 AM digest (max 10 crops). Deleted from device on successful delivery.
- **Limitation:** No on-device species classification yet.
- **v2:** Batch species ID at 3 AM using cached crops.

### 4. Web Dashboard
- **Current:** Setup and calibration require SSH terminal access.
- **v2:** `rook-dashboard` (Next.js + Supabase + Vercel) for remote `.env` management, viewfinder, and threshold tuning without SSH. Model provenance view backed by the `model_card.json` manifests produced by the custom-training pipeline (maps 1:1 onto a `model_versions` table).

### 5. Custom Detection Vocabulary — **Pipeline implemented**
- **Current:** LLM auto-label pipeline ([design](llm_autolabel_pipeline.md)) turns the unclassified archive + Beast Cam crops into training data with no manual annotation. A 30-class local vocabulary (IDs 80–109, append-only): granular vehicles (trash truck, street sweeper, UPS/FedEx/Amazon/USPS/DHL, school bus, police/fire/ambulance, work truck), `baseball_player`, specific wildlife (coyote, fox, deer, raccoon, opossum, skunk, squirrel, rabbit, wild turkey, Canada goose, raptor, cardinal, blue jay), natural phenomena (downed tree, smoke, flood), and curbside objects (trash bins — schedule-driven alerts). All VLM-promoted labels pass a **human review gate** (`review_custom_labels.py`) before training — the Jul 2026 audit measured 1/30 precision on unreviewed whole-frame wildlife finds and retired `flood` from screening. Teacher detector (YOLO26l/x) draws boxes and a vision LLM classifies crops; zero-detection frames get whole-frame VLM screening with approximate boxes. Fine-tune + release gate + NCNN export via `train_custom_model.py`; versioned deploy with health-check rollback via `deploy_model_to_pi.sh`. Engine maps are pre-wired and inert until a custom model is live.
- **Closes:** the `LINGER_THRESHOLDS` truck gap — trash/delivery trucks alert on positive identification (score path), so the 2–5 min stop cycle no longer needs a lingering threshold.
- **Relates to §2 Wildlife Species Resolution:** confirmed species detection at inference time supersedes the COCO-proxy heuristics (solo-dog≈coyote, sheep/cow→deer, HSV bird colors) and complements the post-Hailo EfficientNet plan — coarse species live now, finer species ID later.

---

## Resolved Issues (closed)

| Issue | Resolution |
|---|---|
| B0444 produces I2C errors on CAM/DISP 0 | Use **CAM/DISP 1** — Pivariety MCU only enumerated on `cam1` |
| `dtoverlay=imx462` fails | B0444 requires `dtoverlay=arducam-pivariety,cam1` + Arducam patched libcamera |
| Slack/email blocking main loop | All alert dispatch moved to async daemon threads |
| BGR→RGB color confusion in YOLO | Explicit `cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)` before inference |
| Engine dies on reboot | `rook.service` systemd unit — enabled, auto-restarts on crash |
| MOG2 zone masking (tree) | **Removed from roadmap** — tree contains wildlife, masking defeats purpose |
| Car-only notifications | `SILENT_SOLO_CLASSES = {"car", "bicycle"}` — counted in stats, not alerted |
| Daily digest at 6 PM interrupts captures | Moved to 3 AM (`DIGEST_HOUR = 3`) — lowest activity window |
| Digest showed zero activity (12am–3am window) | Fixed: digest now snapshots the **previous** calendar day at midnight rollover; sent at 3 AM it covers the full day |
| Raw log in digest email was noisy | Removed from digest body — log remains on device at `~/rook.log` |
| 🚂 Train emoji firing despite no rail nearby | `"train"` added to `IGNORED_CLASSES` — suppressed at detection stage, never scored or alerted. Likely a boxy dark vehicle misclassification at distance. |
| Real-time alerts firing too liberally | Both `MIN_EMAIL_SCORE` and `MIN_SLACK_SCORE` aligned to 30. Eliminates routine walk-bys, restricting alerts to high-relevance events. |
| No in-process diagnostic image send | `send_test_email(cam)` added to engine — set `TEST_EMAIL=1` in `.env` to receive a live frame on next restart. `frame_test.py --email` remains the preferred standalone test tool. |
| 🧯 `[fire hydrant]` (and stop sign, parking meter, bench) triggering notifications | Added to `IGNORED_CLASSES` — permanent street/park fixtures will never loiter in or out of a scene. Suppressed at detection stage before scoring or alerting. |
| Cumulative stats static across time windows | Fixed `_window` date comparison bug by parsing text labels to ISO format, and updated stats database to store `date_iso` natively. |
| Inaccurate event counts (e.g. low pedestrians) | Refactored daily stats tracker to operate on distinct events (`new_classes`) instead of per-frame detections, accurately tracking all non-notification events without artificial inflation. |

---

## Open: Lingering Object Tracking

See proposed design in [Rook — Project Overview §8: Lingering Object Detection](../../Rook%20-%20Project%20Overview.md).

**Problem:** Objects that enter the frame and then become static (parked cars, loitering pedestrians) stop triggering MOG2 motion after the background model absorbs them (~200 frames / ~30s). They silently disappear from detection without a "still present" signal.

**Proposed approach — Scene Diff Tracker (low compute, no extra inference):**
- At each YOLO inference event, record the set of detected classes + approximate bbox centroids with a timestamp → `scene_snapshot`
- On the **next** inference pass (triggered by fresh motion nearby), compare current YOLO output against `scene_snapshot`
- If an object class was present in the previous snapshot **and is still present** in roughly the same screen zone → it has been lingering
- After a configurable threshold (e.g. 5 min for vehicles, 2 min for persons), fire a **Lingering Alert**: `🚗🔒 Parked vehicle (15 min)` or `🚶⏱️ Stationary person (8 min)`
- **No extra inference cost** — reuses existing YOLO results; only a dict comparison per cycle
- **Thermal safety** — comparisons are O(n) on a tiny dict; negligible heat impact
- **MOG2 gap** — a periodic forced-inference timer (e.g. every 5 min, bypass MOG2 gate) ensures we can still detect lingering objects even when background has absorbed them

**Status:** Implemented — `LingererTracker` and `SceneFixtureFilter` live in `rook_engine.py`.

**Thresholds (confirmed):**
| Class | Threshold | Loitering Emoji |
|---|---|---|
| `car` | 60 min | `🚗🔒` Parked car |
| `truck` | 60 min | `🗑️🚚` Trash/utility truck |
| `motorcycle` | 30 min | `🏍️🔒` Parked bike |
| `bicycle` | 30 min | `🚲🔒` Unattended bike |
| `person` | 5 min | `🚶⏱️` Loitering individual |
| `work_truck` (custom model) | 60 min | `🛻🔒` Contractor on site |
| `trash_bins` (custom model) | 12 h | `🚮⏱️` Bins still at the curb |

**Lifecycle example — Trash Day:**
1. ~6 AM: Resident wheels bins to curb → MOG2 fires → YOLO detects person + motion → `🚶` alert
2. 6 AM – 8 AM: Bins sit static → MOG2 absorbs them → forced YOLO every 5 min sees `truck` at curb
3. After 60 min: `🗑️🚚 Truck lingering 63min` → Slack alert (trash truck confirmed)
4. ~8 AM: Truck arrives, picks up bins, drives away → fresh motion → `🚚` alert + lingerer evicted

**Re-alert backoff:** exponential per object — 15 min after the first alert, doubling each re-alert (15m → 30m → 1h → 2h → 4h cap). A car parked all day yields ~6 notifications instead of ~30; eviction resets the schedule.

**SceneFixtureFilter:** Auto-suppresses any (class, zone) appearing in ≥80% of 60 consecutive inferences. Resets daily. The houselight `🚦 traffic light` misclassification self-suppresses after ~60 motion events.

---

## Digest: 24h Coverage Confirmation

**Confirmed — digest covers the full previous calendar day (midnight→midnight).**

- Daily stats (`traffic`, `pedestrians`, `animals`, `deliveries`, `total_events`) accumulate from midnight to midnight.
- At the midnight rollover, all stats are snapshotted into `prev_day_*` variables.
- The 3 AM digest sends `prev_day_stats` — NOT the current day's sparse 3-hour window.
- **Emoji activity log** (`daily_emoji_log`): every dispatched alert and lingering event appended as `("HH:MM", emoji_string)`. Snapshotted at midnight alongside stats.
- Digest email renders two views:
  - **Compact stack:** `🚶 · 🚶👥 · 🗑️🚚 [lingering 47min] · 🦅 · 🌙🚶` (scannable at a glance)
  - **Detailed breakdown:** timestamped per-event list
