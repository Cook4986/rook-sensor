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
