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
- **Is it upside down?** If the landscape framing is correct but the world is upside down, do *not* physically re-mount it. Simply toggle the software rotation by adding `FLIP_180=0` or `FLIP_180=1` to your `~/rook-env/.env` file.
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

## Tuning: libcamera IPA file and color processing (added 2026-08-16)

The B0444 (IMX462 Pivariety) makes libcamera log
`Configuration file 'arducam-pivariety.json' not found for IPA module 'rpi/pisp'` at
startup. Arducam calls this cosmetic — tuning parameters live in the camera's onboard
MCU and the driver falls back to them. In practice (2026-08-16 A/B, frames in the
workspace `Media/` as `before_tuning.jpg`/`after_tuning.jpg`) the MCU fallback produced
acceptable bright-daylight color.

We installed the community-standard file anyway, because a file on disk is inspectable
and editable where MCU-internal tuning is not. We initially used `imx290.json` since
IMX462 is nominally the IMX290 sensor family, but that file only ships basic greyworld
AWB and one fixed CCM — it produced a persistent magenta/pink cast at all times of day,
not just dawn/dusk (see `DECISIONS.md` D14). **Updated 2026-08-18:** switched to the
sensor-specific `imx462.json`, which the Pi already had on disk unused — it has full
Bayesian AWB and multiple CCMs across color temperatures, and visibly fixed the cast:

```bash
sudo cp /usr/share/libcamera/ipa/rpi/pisp/imx462.json \
        /usr/share/libcamera/ipa/rpi/pisp/arducam-pivariety.json
```

If a color cast reappears, edit that file directly: `rpi.awb` and `rpi.alsc` (lens
shading; can be disabled by renaming to `disable.rpi.alsc`) are the levers. Delete the
file to revert to MCU fallback, or `cp imx290.json arducam-pivariety.json` to go back to
the old (worse) tuning.

Facts ruled in/out (2026-08-16): the B0444 has an **integral IR-cut filter** (visible
light only, per Arducam spec), so raw IR contamination is unlikely — though the 141°
lens can leak some IR at steep angles near frame edges. The dominant image-quality
problem visible in the A/B frames was **window glare/reflections** from a lens hood not
flush against the glass (see the Phase 2 checklist above) — fix that physically before
chasing color further.
