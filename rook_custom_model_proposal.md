# Rook Custom Model Training Proposal

To ensure we don't miss specific urban/suburban events like delivery trucks, garbage collection, and sports games, we need to move beyond the generic 80 COCO classes. YOLO26n is excellent out of the box, but it cannot differentiate between an Amazon van and a generic box truck, or recognize when a person is in a baseball uniform.

Here is a proposed pipeline to deploy custom YOLO extensions for Rook:

## 1. Data Mining & Curation
Rook is already saving unclassified "ghost motion" frames to `~/rook-archive/unclassified/` and archiving them to Dropbox. We can augment this by temporarily lowering the score threshold for vehicles and people to capture a baseline dataset of the specific scenes we want to classify:
- **Delivery Vehicles**: UPS, FedEx, Amazon, USPS.
- **Municipal**: Trash collection trucks, street sweepers.
- **Sports**: Baseball players (uniforms/equipment), soccer games.

## 2. Annotation Pipeline
Use a tool like **Roboflow** or **CVAT** to annotate the bounding boxes. We should start with a dataset of 300-500 diverse examples (different lighting, weather, and angles) of the target classes.

## 3. Fine-Tuning YOLO26n
We will perform transfer learning on the base `yolo26n.pt` model. Since we are running on a Raspberry Pi 5, we must maintain the `n` (nano) architecture to keep the ~150ms inference time.

```python
from ultralytics import YOLO

# Load the pre-trained base model
model = YOLO('yolo26n.pt')

# Fine-tune on our custom dataset
results = model.train(
    data='rook_custom_classes.yaml',
    epochs=100,
    imgsz=1088,   # Match current Rook resolution
    device='cpu'  # Or use an M-series Mac/Nvidia GPU for training
)
```

## 4. NCNN Export & Deployment
To retain the 3x performance boost on the Pi's CPU, the fine-tuned model must be exported to NCNN format:
```bash
python3 -c "from ultralytics import YOLO; YOLO('runs/detect/train/weights/best.pt').export(format='ncnn', imgsz=1088)"
```
Deploy the resulting NCNN folder to `~/yolo26n_1088_ncnn_model` on the Pi.

## 5. System Integration Updates
Once the model is deployed, we will update `rook_engine.py`:
- Add the new classes to `SCORE_MAP` and `EMOJI_MAP`.
- Create specific heuristics (e.g., if `trash_truck` is detected lingering for > 30 seconds on a Tuesday morning, fire a custom `🗑️🚚 Trash day` alert).
- Add `baseball_player` to the congregation logic to boost scores specifically for sports events.

## 6. Advanced Tracking (Optional)
If the current `LingererTracker` grid-cell heuristic is missing loiterers who pace or drift around the yard, we can integrate a lightweight tracker like **ByteTrack** or **BoT-SORT** (natively supported by Ultralytics). This tracks object IDs across frames, providing a precise dwell time regardless of movement within the frame.
