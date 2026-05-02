# Camera Placement & Calibration Guide

Because Rook is a headless device (no screen), achieving the perfect camera angle and field of view is a conversational, iterative process. 

Follow this guide to use the `FRAME` viewfinder to perfectly align your sensor.

## Phase 1: Physical Orientation

The camera sensor natively captures video in a 16:9 widescreen format. To maximize horizontal coverage of the street or park:

1. Look at the green Arducam circuit board.
2. Ensure the ribbon cable is exiting from either the **top** or the **bottom** of the board.
3. If the cable exits the left or right side, your video will be sideways (portrait mode). Unclip and rotate it 90 degrees.

## Phase 2: The Viewfinder Loop

Rook uses an interactive viewfinder. When you trigger a test, Rook will capture a photo, run AI detection, draw boxes around what it sees, and send it to you.

1. **Mount the camera** temporarily using the suction cups. Angle it slightly downward (~15°) toward the target area.
2. **Request a frame:** Run the test script (or text `FRAME` to the device).
3. **Review the image:** Check your inbox for the annotated photo.

### Evaluation Checklist
- **Is the horizon level?** Adjust the suction cup rotation.
- **Is there indoor glare?** Ensure the lens or silicone hood is pressed *flush* against the glass to block interior room reflections, which will severely degrade nighttime performance.
- **Is the target visible?** Ensure the street/sidewalk is in the center third of the image.

## Phase 3: Lens Selection (Field of View)

Rook ships with a **141° Ultra-Wide lens** pre-installed. This is excellent for initial alignment, but it makes objects in the distance appear very small, which can reduce AI detection confidence.

Look at your test image:
- **If your target (e.g., the street/crosswalk) is less than 10 meters (30 ft) away:** The ultra-wide lens is perfect. Leave it installed.
- **If your target is further than 10 meters away:** The target may be too small for reliable detection. You should swap out the wide lens for the **8mm (~40° HFOV)** operational lens.
    - *How to swap:* Unscrew the 141° lens counter-clockwise. Screw in the 8mm lens. Run the `FRAME` test repeatedly, making micro-adjustments to the lens thread until the image is perfectly in focus. 

## Phase 4: Zone Masking (Noise Reduction)

To save processing power, Rook ignores background motion (like trees swaying in the wind) before it ever runs the AI. 

1. Look at your final, perfectly framed test image.
2. Identify areas of continuous motion that you *do not* care about (e.g., an ornamental tree on the right side, a flag flapping on the left).
3. Take note of what percentage of the screen these occupy (e.g., "The tree takes up the right 30% of the frame").
4. This information will be entered into your Rook configuration to establish a **Zone Mask**, telling the motion detector to completely ignore that section of the screen.

Once you are happy with the framing, focus, and masking, you are ready to begin active monitoring!
