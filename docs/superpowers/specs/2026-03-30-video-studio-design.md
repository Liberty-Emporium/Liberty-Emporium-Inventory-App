# Video Studio — Design Spec
**Date:** 2026-03-30
**Status:** Approved by user

---

## Overview

Add a **Video Studio** panel to the existing Ad Generator page (`/ads`). Users select products, pick a video style, choose a music track (or upload their own), set a duration, then click **Create Video Ad**. The server uses ffmpeg to compose an MP4 and returns it for preview and download — no external APIs required.

---

## User Flow

```
Ad Generator page
  1. Select products (existing behavior — unchanged)
  2. Pick Ad Style (existing behavior — unchanged)
  3. Click "✨ Generate Image Ads" (existing — unchanged)
       ↓
  [Video Studio panel — new, below the sticky generate bar]
  4. Pick Video Style: Slideshow | Ken Burns
  5. Pick Music: select from library card OR upload own MP3
  6. Set Duration: slider 10–60 seconds
  7. Click "🎬 Create Video Ad"
       → POST /generate-video-ad
       → ffmpeg runs on server
       → MP4 saved to ads/ folder
       → Video results section updates with player + download button
```

---

## UI Components

### Video Studio Panel
- Purple gradient header (`#7c3aed → #4f46e5`) with "🎬 Video Studio" title and "NEW" badge
- Sits below the existing sticky generate bar on the Ad Generator page
- Does not replace or modify any existing UI

### Video Style Picker
- Two cards: **Slideshow** (🎞️) and **Ken Burns** (🎥)
- Same interaction pattern as the existing Ad Style picker
- Default: Slideshow

### Music Section
- Grid of cards, one per track in the `music/` folder
  - Each card shows track name, filename, and a ▶️ preview button (HTML5 audio, plays in browser)
  - Selected card gets purple border highlight
- Upload zone below the grid: dashed purple border, accepts `.mp3` only
  - Uploaded file is sent with the video generation request (multipart)
  - Does not permanently save to server — used for this generation only
- One track must be selected before generating (either library or upload)

### Duration Slider
- Range: 10–60 seconds
- Default: 30 seconds
- Live label shows current value: "⏱️ Video Duration — 30 seconds"

### Create Video Ad Button
- Full-width, purple gradient, "🎬 Create Video Ad"
- Disabled + shows spinner while generating
- Uses the same selected products already chosen for image ads
- At least one product must be selected

### Video Results Area
- Appears below the Video Studio panel
- Each generated video shown as a card with:
  - HTML5 `<video>` player (autoplay muted loop for preview)
  - Product name label
  - ⬇️ Download MP4 button

---

## Backend

### New Route: `POST /generate-video-ad`
**Auth:** `@login_required`
**Input (multipart/form-data):**
```
products   JSON string — list of {sku, title, price, image, description}
style      "slideshow" | "kenburns"
duration   integer, 10–60
music_track  filename of selected library track (e.g. "audiogreen-phonk-167055.mp3")
music_file   uploaded MP3 file (optional — used instead of music_track if provided)
```

**Processing per product (using ffmpeg + Pillow):**
1. Load product image (or generate a solid-color placeholder if none)
2. Use Pillow to render a frame image: product photo + title + price text overlay
3. Call ffmpeg to:
   - Loop/hold the frame image for the requested duration
   - Apply Ken Burns effect (zoompan filter) if selected
   - Mix in the music track, trimmed to duration
   - Output as MP4 (H.264 video, AAC audio)
4. Save to `ads/` as `video_ad_{sku}_{timestamp}.mp4`

**Output (JSON):**
```json
{
  "success": true,
  "files": [
    { "filename": "video_ad_SKU_20260330_120000.mp4", "product_title": "Vintage Vase" }
  ]
}
```

### Music Library Route: `GET /music`
Returns JSON list of available tracks from the `music/` folder:
```json
[
  { "filename": "audiogreen-phonk-167055.mp3", "display_name": "Phonk Beat" },
  ...
]
```
Display names are derived by: strip file extension → replace `-` and `_` with spaces → title-case → truncate to 40 chars. Example: `audiogreen-phonk-167055.mp3` → `Audiogreen Phonk 167055`.

### Music Serve Route: `GET /music/<filename>`
Serves MP3 files from the `music/` folder for browser preview playback.

---

## ffmpeg Command Structure

### Slideshow style
```bash
ffmpeg -loop 1 -i frame.jpg -i music.mp3 \
  -c:v libx264 -tune stillimage -c:a aac \
  -t {duration} -pix_fmt yuv420p \
  output.mp4
```

### Ken Burns style
Uses 25 fps. `d` = duration × 25 (total frames).
```bash
ffmpeg -loop 1 -i frame.jpg -i music.mp3 \
  -vf "zoompan=z='min(zoom+0.0015,1.5)':d={duration*25}:s=1280x720,fps=25" \
  -c:v libx264 -c:a aac \
  -t {duration} -pix_fmt yuv420p \
  output.mp4
```

---

## File & Folder Structure

```
ads/                    # existing — image ads + new MP4s land here
music/                  # existing — library MP3 files
  audiogreen-phonk-167055.mp3
  deltax-music-vice-city-vibes-grand-theft-auto-style-soundtrack-301060.mp3
  Gigi_Perez_-_Sailor_Song_Official_Music_Video.mp3
  hhh.mp3
```

No new folders needed.

---

## Error Handling

- No products selected → button disabled, client-side guard
- No music selected → client-side validation before submit
- ffmpeg not found → return `{"error": "Video generation not available on this server"}`
- Product has no image → use a solid-color placeholder frame (Pillow)
- Uploaded MP3 too large (>20 MB) → reject with clear error message
- ffmpeg failure → return stderr in error response for debugging

---

## Out of Scope

- Adding/deleting tracks from the music library via the UI (admin can manage files directly)
- Multi-product videos (one product per video — same as image ads)
- Video trimming or editing after generation
- Permanent storage of uploaded music tracks
