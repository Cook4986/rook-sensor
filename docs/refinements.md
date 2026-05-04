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
- **v2:** `rook-dashboard` (Next.js + Supabase + Vercel) for remote `.env` management, viewfinder, and threshold tuning without SSH.

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
| Real-time emails firing too liberally | `MIN_EMAIL_SCORE` raised 20→30. Slack retains lower threshold (≥8) for broader coverage. |
| No in-process diagnostic image send | `send_test_email(cam)` added to engine — set `TEST_EMAIL=1` in `.env` to receive a live frame on next restart. `frame_test.py --email` remains the preferred standalone test tool. |
| 🧯 `[fire hydrant]` (and stop sign, parking meter, bench) triggering notifications | Added to `IGNORED_CLASSES` — permanent street/park fixtures will never loiter in or out of a scene. Suppressed at detection stage before scoring or alerting. |

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

**Lifecycle example — Trash Day:**
1. ~6 AM: Resident wheels bins to curb → MOG2 fires → YOLO detects person + motion → `🚶` alert
2. 6 AM – 8 AM: Bins sit static → MOG2 absorbs them → forced YOLO every 5 min sees `truck` at curb
3. After 60 min: `🗑️🚚 Truck lingering 63min` → Slack alert (trash truck confirmed)
4. ~8 AM: Truck arrives, picks up bins, drives away → fresh motion → `🚚` alert + lingerer evicted

**Re-alert cooldown:** 15 min per object — prevents spam if truck idles.

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
