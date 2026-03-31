# Video Ad Redesign Spec
Date: 2026-03-31

## Goal
Make generated video ads look like real retail ads — bigger text, better layout, and text that fades in cleanly instead of being static.

## Three Changes

### 1. Full-Bleed Layout
The product photo fills the **entire frame** (both horizontal and vertical formats). A dark gradient overlay covers the bottom 40% of the frame. All text renders on top of that gradient. No more split left/right panel.

- Gradient: opaque dark at the bottom edge, fading to transparent at ~60% height
- Uses the template's `bg_dark` color as the gradient base
- Product image scaled to fill frame (cover crop, centered)

### 2. Text Hierarchy Redesign
Text rendered top-to-bottom inside the gradient zone:

| Element | Font size formula | Min | Color |
|---|---|---|---|
| Store name | `W * 0.016` | 18px | Dimmed white (160,160,200) |
| Product title | `W * 0.048` | 48px | Accent color |
| Price | `W * 0.072` | 64px | White, bold |
| Description | `W * 0.020` | 20px | (200,200,220) |
| CTA text | `W * 0.026` | 24px | Accent color |

Title and price are roughly 2–3x larger than current values. Price is the visual anchor.

### 3. Text Fade-In via FFmpeg Overlay
Two images generated per frame:
- `bg.jpg` — product photo + gradient, **no text**
- `text.png` — transparent PNG with only the text drawn on it

FFmpeg command uses the `overlay` filter with `fade` to blend `text.png` over `bg.jpg`:
```
[0:v][1:v]overlay=0:0:enable='gte(t,1)':format=auto,fade=in:st=1:d=0.8:alpha=1
```
- Text invisible for first 1 second
- Fades in over 0.8 seconds
- Background (with Ken Burns zoom if selected) plays throughout

Both slideshow and kenburns styles get the fade. Ken Burns zoom applies to the background layer only.

## Files Changed
- `app_with_ai.py` — `generate_video_ad()` function only
  - New `_draw_text_layer()` helper that returns a transparent PIL Image
  - Updated ffmpeg command to use two inputs + overlay filter
  - Updated font size constants
  - Updated layout to full-bleed

## Out of Scope
- No changes to the UI (ad_generator.html)
- No changes to seasonal color schemes
- No new settings or options exposed to the user
