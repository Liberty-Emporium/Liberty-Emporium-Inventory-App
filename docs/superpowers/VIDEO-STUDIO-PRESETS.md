# Video Studio — Quick Presets & Progress Tracking
**Updated:** March 31, 2026  
**Status:** Preset Combinations & Live Progress Indicator Added

---

## Quick Presets Overview

**6 pre-configured preset combinations** that instantly apply optimized settings with one click. Perfect for users who want to generate videos quickly without manually tweaking every setting.

### Available Presets

#### ⚡ Quick 30-Second Ad
- **What it does**: One-click video generation with sensible defaults
- **Template**: Default (Liberty Emporium gold & navy)
- **Format**: YouTube (1920×1080) - 16:9 horizontal
- **Style**: Slideshow (smooth, fast rendering)
- **Duration**: 30 seconds
- **Perfect for**: First-time users, quick social posts
- **Generation time**: ~20-30 seconds per product

#### 📱 TikTok/Reels Video
- **What it does**: Vertical video optimized for mobile social platforms
- **Template**: Default
- **Format**: TikTok (1080×1920) - 9:16 vertical
- **Style**: Slideshow
- **Duration**: 15 seconds (ideal for loops)
- **Perfect for**: TikTok, Instagram Reels, YouTube Shorts
- **Generation time**: ~15-25 seconds per product
- **Pro tip**: Auto-stacks product image on top, text overlay at bottom

#### 🎄 Holiday Campaign Bundle
- **What it does**: Generate video in ALL 5 social media formats simultaneously
- **Template**: Holiday (red & green festive colors)
- **Format**: All (generates 1920×1080, 1080×1350, 1080×1920, 1200×628, 1080×1080)
- **Style**: Slideshow
- **Duration**: 30 seconds per format
- **Perfect for**: Holiday campaigns, coordinated multi-platform launch
- **Generation time**: ~2-3 minutes per product (5 videos total)
- **Files generated**: 5 videos with different aspect ratios, all branded with Holiday theme

#### 🖼️ Store Display Loop
- **What it does**: Square video perfect for kiosk or tablet in-store loops
- **Template**: Default
- **Format**: Square (1080×1080) - 1:1 ratio
- **Style**: Slideshow
- **Duration**: 30 seconds
- **Perfect for**: In-store displays, tablets, digital signage
- **Generation time**: ~20-30 seconds per product
- **Pro tip**: Ideal for looping — 2 videos per minute, 120 per hour of display

#### ▶️ Full YouTube Video
- **What it does**: Premium format with cinematic effect for YouTube embedding
- **Template**: Default
- **Format**: YouTube (1920×1080) - 16:9 horizontal
- **Style**: Ken Burns (slow cinematic zoom)
- **Duration**: 60 seconds
- **Perfect for**: Website embeds, YouTube uploads, professional presentations
- **Generation time**: ~40-60 seconds per product (Ken Burns takes longer)
- **Pro tip**: Best quality, most professional look

#### 👍 Facebook Share Preview
- **What it does**: Optimized for Facebook link previews and shares
- **Template**: Default
- **Format**: Facebook (1200×628) - 16:9 horizontal
- **Style**: Slideshow
- **Duration**: 20 seconds
- **Perfect for**: Facebook posts, link shares, embedded feeds
- **Generation time**: ~15-25 seconds per product
- **Pro tip**: Fits Facebook's optimal link preview dimensions exactly

---

## Using Presets

### Step-by-Step

1. **Select your products** from the grid (up to 10)
2. **Click any Quick Preset** button:
   ```
   ⚡ Quick 30s  |  📱 TikTok/Reels  |  🎄 Holiday  |  
   🖼️ Display    |  ▶️ YouTube         |  👍 Facebook
   ```
3. **Watch the button highlight** — settings auto-populated!
4. Confirmation appears: `✅ Preset applied: [Name]`
5. **Optional**: Customize any remaining settings (CTA text, logo, music track)
6. **Click 🎬 Create Video Ad** to generate
7. Watch the **progress bar** fill up in real-time
8. Download your video when complete

### Example: Holiday Campaign in 5 Minutes

```
1. Select 3 products from inventory
2. Click 🎄 "Holiday Campaign Bundle"
   ✅ Auto-sets: Holiday template + All 5 formats
3. Select a festive music track from library (or upload)
4. Click "Create Video Ad"
5. Progress bar shows: "Processing format 1/5..." → "2/5" → ... → "5/5"
6. Wait ~2-3 minutes
7. Download 15 videos (3 products × 5 formats each)
8. Ready to post across all platforms simultaneously!
```

---

## Live Progress Indicator

When you click **🎬 Create Video Ad**, a real-time progress overlay shows exactly what's happening:

### Progress Display Components

```
┌─────────────────────────────────────┐
│         🔄 Spinner Animation         │
├─────────────────────────────────────┤
│  Creating 5 video formats...         │  ← Current task
├─────────────────────────────────────┤
│  ████████████░░░░░░░░░░░░░░   67%   │  ← Progress bar
├─────────────────────────────────────┤
│  Processing frame... (format 3/5)   │  ← Real-time status
└─────────────────────────────────────┘
```

### Progress Stages

The progress indicator updates in real-time through these stages:

**Stage 1: Initializing**
```
Initializing...
- Setting up video generation environment
- Loading configuration
- Estimated: 5-10% progress
```

**Stage 2: Processing Frames**
```
Processing frames...
- Creating image composites
- Rendering text overlays (title, price, CTA)
- Compositing logo/watermark
- Estimated: 30-60% progress
- For multi-format: Shows "Processing format X/Y"
```

**Stage 3: Encoding Videos**
```
Encoding videos...
- Running FFmpeg compression
- Ken Burns zoom interpolation (if selected)
- Audio mixing and sync
- Estimated: 60-95% progress
```

**Final: Complete**
```
Complete!
- Video(s) saved to server
- Ready for download
- 95-100% progress
```

### Multi-Format Progress Example

When generating the Holiday bundle (all 5 formats):

```
Start:
  Loading Overlay: "Creating 5 video formats..."
  Progress Bar: 0%
  Detail: "Initializing..."

After 5 seconds:
  Detail: "Processing frames..."
  Progress Bar: 10%

After 15 seconds:
  Detail: "Processing format 1/5..."
  Progress Bar: 20%

After 30 seconds:
  Detail: "Encoding videos..."
  Progress Bar: 45%

After 60 seconds:
  Detail: "Processing format 2/5..."
  Progress Bar: 55%

[... continues through all 5 formats ...]

After 150 seconds:
  Detail: "Complete!"
  Progress Bar: 100%
  Loading Overlay: Closes
  Results: 5 videos displayed below
```

### What Each Stage Tells You

- **Initializing** — System preparing; if stuck here >10 seconds, check internet connection
- **Processing frames** — Creating thumbnail/frame images; if stuck >30 seconds, check product image quality
- **Encoding videos** — FFmpeg running; usually 30-90 seconds depending on video style and resolution
- **Multi-format note** — Each format adds ~30-60 seconds (totaling 2-3 minutes for all 5)

---

## When to Use Each Preset

### Quick 30s
✅ **Use when:**
- Testing video generation
- Want fastest possible video
- Default branding is fine
- YouTube posting

❌ **Skip when:**
- Need vertical video (use TikTok preset)
- Want cinematic effect (use YouTube preset)
- Need multiple formats (use Holiday preset)

### TikTok/Reels
✅ **Use when:**
- Creating TikTok content
- Posting to Instagram Reels
- YouTube Shorts format
- Mobile-first audience

❌ **Skip when:**
- Need wide-screen display
- Posting to YouTube direct
- Desktop viewing

### Holiday Campaign Bundle
✅ **Use when:**
- Launching across all platforms simultaneously
- Have consistent message for all channels
- Want seasonal branding (red & green)
- Large campaign coordinated rollout

❌ **Skip when:**
- Only need one format
- Want different templates per platform
- Time-sensitive (multi-format takes 2-3 min)

### Store Display Loop
✅ **Use when:**
- Creating in-store kiosk/tablet video
- Looping on digital signage
- Want square format
- Continuous play scenario

❌ **Skip when:**
- Posting to social media
- Need wide-screen format
- Watching on desktop browser

### Full YouTube Video
✅ **Use when:**
- Uploading to YouTube channel
- Creating premium embed for website
- Want cinematic Ken Burns effect
- Have 60+ seconds to show product

❌ **Skip when:**
- Time-constrained (generation slower)
- Need quick loop playback
- Want fast rendering (Ken Burns slower)

### Facebook Share Preview
✅ **Use when:**
- Sharing on Facebook
- Want optimized link preview
- Only posting to Facebook
- Professional social presence

❌ **Skip when:**
- Multi-platform campaign
- Need vertical video
- Want YouTube specs

---

## How Presets with Custom Overlays Work

Presets auto-fill **template, format, style, and duration**, but you can still customize:

**Auto-filled by preset ⚡**
- Template (e.g., Holiday)
- Format (e.g., 1080×1920)
- Video style (Slideshow/Ken Burns)
- Duration (e.g., 30 seconds)

**Still editable by you ✏️**
- CTA Text (e.g., "Holiday Special $9.99!")
- Tagline (e.g., "Shop Liberty Emporium")
- Logo/Watermark (upload your logo)
- Music track (library or custom upload)

**Example workflow:**
```
1. Click 🎄 Holiday Campaign Bundle preset
2. Duration = 30s, Template = Holiday, Format = All ✓
3. Customize CTA: "🎁 Holiday Special Sale!"
4. Customize Tagline: "Liberty Emporium & Thrift"
5. Upload logo with Medium size + Top Right position
6. Select festive music track (or upload)
7. Click Create Video Ad
8. Get 5 branded Holiday videos with your custom text/logo!
```

---

## Preset Combinations Reference

| Preset | Template | Format | Style | Duration | Use Case |
|--------|----------|--------|-------|----------|----------|
| Quick 30s | Default | 1920×1080 | Slideshow | 30s | Fast social posts |
| TikTok/Reels | Default | 1080×1920 | Slideshow | 15s | Mobile vertical |
| Holiday Bundle | Holiday | All (5) | Slideshow | 30s | Multi-platform campaign |
| Store Display | Default | 1080×1080 | Slideshow | 30s | In-store kiosk loops |
| YouTube Video | Default | 1920×1080 | Ken Burns | 60s | Premium embedding |
| Facebook Share | Default | 1200×628 | Slideshow | 20s | Social link preview |

---

## Time Estimates

### Single Product, Single Format
| Preset | Generation Time |
|--------|-----------------|
| Quick 30s | 20-30 sec |
| TikTok/Reels | 15-25 sec |
| Store Display | 20-30 sec |
| Facebook Share | 15-25 sec |
| YouTube Video | 40-60 sec (Ken Burns) |

### Multiple Products
- Each product adds estimated time to total
- Example: 3 products × Quick 30s ≈ 60-90 seconds total

### Holiday Bundle (All Formats)
- 1 product × 5 formats ≈ 2-3 minutes
- 2 products × 5 formats ≈ 4-6 minutes
- 3 products × 5 formats ≈ 6-9 minutes

---

## Troubleshooting Presets

| Issue | Solution |
|-------|----------|
| Preset doesn't apply | Make sure at least 1 product selected; click preset again |
| Settings not changing | Scroll down to see updated template/format selections |
| Progress bar stuck | Check internet connection; if >2 min idle, refresh page |
| Video quality looks low | Check product image quality; preset can't fix blurry source |
| Multi-format taking too long | Holiday bundle generates 5 videos (2-3 min normal) |

---

## Pro Tips

### Tip 1: Batch Content Creation
```
Generate 6 preset videos (1 per preset) for complete library:
1. Select 1 product
2. Quick 30s preset → 30 sec
3. TikTok preset → 25 sec
4. Store Display preset → 30 sec
5. YouTube preset → 60 sec
6. Facebook preset → 25 sec
Total: ~3 minutes, 6 videos ready to post!
```

### Tip 2: Campaign Planning
```
Use presets to match your campaign timeline:
- Monday: Holiday preset (all formats) for week-long social push
- Wednesday: Quick 30s for mid-week engagement
- Friday: YouTube preset for weekend views
```

### Tip 3: A/B Testing
```
Generate same product with different presets:
- Holiday preset for festive feel
- Quick 30s for professional tone
Post both, see which performs better!
```

### Tip 4: Seasonal Rotation
- **January-February**: Holiday Bundle 
- **March-May**: Spring template (custom preset possible future)
- **June-August**: Summer template
- **September-October**: Back-to-School template
- **November**: Black Friday template
- **December**: Holiday Bundle

---

## Preset File Naming

Generated videos include preset information in filename:

```
video_ad_{SKU}_{TEMPLATE}_{FORMAT}_{TIMESTAMP}.mp4
```

**Examples from presets:**
- `video_ad_CHAIR-001_default_1920x1080_20260331_142530.mp4` (Quick 30s)
- `video_ad_VASE_default_1080x1920_20260331_143015.mp4` (TikTok)
- `video_ad_LAMP_holiday_1080x1350_20260331_143530.mp4` (Holiday Bundle)
- `video_ad_RUG_default_1080x1080_20260331_144015.mp4` (Store Display)

---

## Future Preset Ideas

Potential additional presets (not yet built):
- [ ] **Custom Preset Builder** — Users create/save their own combinations
- [ ] **Seasonal Auto-Rotate** — Automatically switch templates by date
- [ ] **Platform Packages** — YouTube + Instagram combo preset
- [ ] **Fast & Furious** — 10-second ultra-short format
- [ ] **Extended Cut** — 3-5 minute product showcase preset
- [ ] **Trending Audio** — Preset with popular trending music

---

## Video Generation Architecture

```
User clicks Preset
    ↓
Preset configuration loaded
    ↓
If format = 'all':
    └─→ Loop through [1920×1080, 1080×1350, 1080×1920, 1200×628, 1080×1080]
    └─→ For each format:
        ├─→ POST /generate-video-ad with format
        ├─→ Backend renders frame (adapted to format size)
        ├─→ FFmpeg encodes video
        ├─→ Return video filename
        └─→ Update progress bar + detail text
    └─→ All formats complete
    └─→ Display results
Else:
    └─→ Single format generation
    └─→ Update progress through 3 stages
    └─→ Display result
    
Progress updates: 0% → 10% → 45% → 90% → 100%
Detail text: "Initializing" → "Processing frames" → "Encoding" → "Complete"
```

---

## Support & Feedback

**Having trouble with presets?**
- ✅ Make sure products are selected
- ✅ Check that music track is chosen
- ✅ Verify internet connection (multi-format needs stable connection)
- ✅ Try a simpler preset first (Quick 30s before Holiday Bundle)

**Want a new preset combination?**
- Contact Liberty Emporium team with your requested settings
- We can add custom presets in future updates
