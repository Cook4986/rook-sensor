# Rook v2: Design & Development Refinements

Based on the prototyping, assembly, and testing phases of the initial Rook build, several hardware and software refinements have been identified. These should be considered for the v2 "Production" build.

## Hardware & Mechanical Refinements

1. **Power Supply & Cabling**
   - **Issue:** The combination of a 15W USB-A QuickCharge 3.0 power brick and a 10-foot flat USB cable resulted in voltage drop (brownouts) when the Pi 5 CPU spiked to 100% during YOLO inference, causing the PMIC to halt the system (solid red LED).
   - **Recommendation:** Use an official Raspberry Pi 27W USB-C PD power supply, or a high-quality USB-C PD source with a short, thick-gauge cable to ensure stable 5V/5A delivery.

2. **Mounting the Arducam B0444**
   - **Issue:** The Arducam IMX462 sensor board is extremely compact (24mm × 25mm) and lacks the standard 29mm mounting holes found on standard Raspberry Pi camera modules.
   - **Recommendation:** A custom 3D-printed enclosure is required. The enclosure must grip the sensor board by its edges or secure the threaded M12 lens barrel directly, as traditional M2/M2.5 standoffs cannot be used on the board itself.

3. **Raspberry Pi 5 Assembly**
   - **Issue:** The Pi 5 requires M2.5 screws/standoffs for its 85mm × 56mm mounting pattern, which were not initially listed in the BOM.
   - **Recommendation:** Add an M2.5 standoff kit to the required hardware list for secure window-mount bracket assembly.

4. **Thermal Management**
   - **Issue:** YOLOv11n inference running on the Cortex-A76 CPU generates rapid heat spikes. While the passive Easycargo heatsink kept idle temps around 37°C, sustained inference pushes it past 46°C.
   - **Recommendation:** If transitioning to a sealed enclosure, passive cooling will be insufficient. We must either add a micro-blower fan (driven by the Pi 5 fan header) or upgrade to the Hailo-8L AI Accelerator HAT+, which offloads inference and drastically reduces SoC thermal load.

## Software & Logic Refinements

1. **Intelligent Exposure Polling (Lux Integration)**
   - **Issue:** Currently, day/night exposure is determined purely by mathematical sunrise/sunset tables (`suntime` package). While highly efficient, this does not account for heavy overcast days or bright artificial streetlights.
   - **Recommendation:** Implement a background thread that periodically polls the camera's actual `Lux` or `AnalogueGain` metadata and adjusts the `ExposureValue` dynamically. This should run independently of the motion loop (e.g., once every 10 minutes) to avoid CPU overhead during active tracking.

2. **Heuristic Grouping Upgrades**
   - **Issue:** Standard COCO datasets classify wildlife poorly (e.g., foxes as dogs, deer as sheep).
   - **Recommendation:** Since retraining YOLOv11 on a custom dataset is expensive, build advanced post-processing heuristics. For example, if YOLO detects a "sheep" or "cow" in a residential suburban zone, the translation layer should aggressively remap it to `🦌` (Deer).

3. **Masking & Ghost Motion Rejection**
   - **Issue:** MOG2 background subtraction is highly sensitive to wind blowing through trees or shadows moving across the street.
   - **Recommendation:** Implement a polygon-based inclusion zone instead of a simple rectangular exclusion mask. Allow the user to draw a geometric polygon (via the web dashboard) specifying *exactly* where the street and sidewalk are.

4. **Web Dashboard Integration (Pending)**
   - **Issue:** Setup and calibration currently require SSH terminal access.
   - **Recommendation:** Complete the `rook-dashboard` Next.js interface. The dashboard should securely interface with a local Flask/FastAPI server on the Pi to stream the viewfinder, manage the `.env` file remotely, and draw the MOG2 exclusion zones.
