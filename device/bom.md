# Bill of Materials

## v1 Prototype BOM

| Part | Model | Vendor | Price |
|------|-------|--------|-------|
| Compute | Raspberry Pi 5 — 2 GB | Amazon | $74.29 |
| Camera | Arducam B0444 IMX462 STARVIS (M12, 141° lens, both CSI cables) | Arducam Direct | $47.99 |
| Storage | SanDisk 32 GB High Endurance microSDHC | Amazon | $22.99 |
| Mounting | Juxiamal 41mm PVC suction cups, M5 bolt, 6-pack | Amazon | $6.99 |
| Thermal | Easycargo 20pc heatsink kit (aluminum + copper + thermal tape) | Amazon | ~$10 |
| USB Cable | Itramax 10ft flat USB-A → USB-C, 2-pack | Amazon | $13.99 |
| Power | Besgoods 5V/3A QC 3.0 USB-A wall charger, 2-pack | Amazon | $10.61 |
| SD Reader | Acer USB-C dual-slot card reader | Amazon | ~$9 |
| | | **Total** | **~$213** |

---

## Purchase Records

### Arducam — Order #000005644
**Date:** April 17, 2026

| Item | SKU | Price |
|------|-----|-------|
| 2MP IMX462 Ultra Low Light STARVIS Camera Module w/ 141° M12 Lens | B0444 | $47.99 |
| Shipping | | $10.00 |
| **Order Total** | | **$57.99** |

### Amazon — Order #111-6169035-6822651
**Date:** April 17, 2026

| Item | Price |
|------|-------|
| Raspberry Pi 5 — 2 GB RAM | $74.29 |
| Itramax 10ft flat USB-A→USB-C cable, 2-pack | $13.99 |
| **Order Total** | **$93.79** |

### Amazon — Order #111-7967764-9140239
**Date:** April 17, 2026

| Item | Price |
|------|-------|
| Juxiamal 6pc suction cups, 41mm PVC, M5 bolt | $6.99 |
| SanDisk 32 GB High Endurance microSDHC | $22.99 |
| **Order Total** | **$31.86** |

### Amazon — Order #111-1323662-9664204
**Date:** April 17, 2026

| Item | Price |
|------|-------|
| Acer USB-C SD card reader (dual-slot) | — |
| Easycargo 20pc Raspberry Pi heatsink kit | — |
| **Order Total** | **$19.10** |

### Amazon — Order #111-2113241-1757067
**Date:** April 17, 2026

| Item | Price |
|------|-------|
| Besgoods 5V/3A QC 3.0 USB-A wall charger, 2-pack | $10.61 |
| **Order Total** | **$10.61** |

---

## Deferred (v2)

| Item | Vendor | Est. Price | Notes |
|------|--------|-----------|-------|
| 8mm M12 CCTV lens (~40° HFOV) | Amazon | ~$8 | Narrower FOV for better distance resolution. Using included 141° lens for v1. |
| Silicone suction cups (41mm, M5) | Amazon | ~$8 | Better UV/heat longevity than PVC. |
| Arducam B0423 (Motorized IR-Cut, f/1.6) | Arducam | ~$55 | Upgrade if nighttime YOLO accuracy is insufficient with fixed IR-cut. |
| Raspberry Pi AI HAT+ (Hailo-8L, 13 TOPS) | Pi-authorized | ~$26 | PCIe snap-on, enables 400+ FPS continuous monitoring. |

---

## Power Notes

- The Pi 5 runs on any **5V/3A** USB-C supply when not driving USB peripherals.
- The Besgoods QC 3.0 charger outputs plain 5V over USB-A (no PD negotiation with the USB-A→USB-C cable) — this is correct and expected.
- If you see the ⚡ undervoltage icon during inference, use a known 5V/3A adapter (e.g., old iPad charger).
- **Budget alternative:** Pi 5 1 GB ($50) is viable with `gpu_mem=16` and a 512 MB swap file.
