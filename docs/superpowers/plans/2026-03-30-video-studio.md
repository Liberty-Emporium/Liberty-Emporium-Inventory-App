# Video Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Video Studio panel to the Ad Generator page that lets users pick a video style, choose or upload a music track, set duration, and generate MP4 video ads using ffmpeg.

**Architecture:** Three new backend routes handle music listing, music serving, and video generation. The existing `ad_generator.html` template gets a new Video Studio panel injected below the sticky generate bar. ffmpeg + Pillow do all video composition server-side — no external APIs.

**Tech Stack:** Flask, Python, Pillow (already installed), ffmpeg (already at `/usr/bin/ffmpeg`), vanilla JS (existing pattern), HTML5 `<video>` + `<audio>` for preview.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `app_with_ai.py` | Modify | Add `MUSIC_FOLDER` constant + 3 new routes |
| `templetes/ad_generator.html` | Modify | Add Video Studio panel HTML, CSS, JS |

---

## Task 1: Add MUSIC_FOLDER constant and music routes to backend

**Files:**
- Modify: `app_with_ai.py` (paths section ~line 24, after existing routes ~line 584)

- [ ] **Step 1: Add MUSIC_FOLDER to the paths block and mkdir call**

In `app_with_ai.py`, find the paths block (around line 24) and add:

```python
MUSIC_FOLDER   = os.path.join(BASE_DIR, 'music')
```

Then find the `for d in [UPLOAD_FOLDER, BACKUP_FOLDER, ADS_FOLDER]:` line and update it:

```python
for d in [UPLOAD_FOLDER, BACKUP_FOLDER, ADS_FOLDER, MUSIC_FOLDER]:
    os.makedirs(d, exist_ok=True)
```

- [ ] **Step 2: Add the `GET /music` route after the `download_ad` route (~line 584)**

```python
# ── Music Library ─────────────────────────────────────────────────────────────
@app.route('/music')
@login_required
def list_music():
    tracks = []
    for fname in sorted(os.listdir(MUSIC_FOLDER)):
        if fname.lower().endswith('.mp3'):
            display = os.path.splitext(fname)[0]
            display = display.replace('-', ' ').replace('_', ' ')
            display = ' '.join(w.capitalize() for w in display.split())
            if len(display) > 40:
                display = display[:40].rsplit(' ', 1)[0]
            tracks.append({'filename': fname, 'display_name': display})
    return jsonify(tracks)


@app.route('/music/<filename>')
@login_required
def serve_music(filename):
    return send_from_directory(MUSIC_FOLDER, filename)
```

- [ ] **Step 3: Verify the routes work**

Start the app locally and run:
```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
python app_with_ai.py &
sleep 2
curl -s http://localhost:5000/music  # expect redirect to login (302) — that confirms route exists
kill %1
```
Expected: HTTP 302 (redirects to login — route registered correctly).

- [ ] **Step 4: Commit**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
git add app_with_ai.py
git commit -m "feat: add MUSIC_FOLDER + GET /music and GET /music/<filename> routes"
```

---

## Task 2: Add `POST /generate-video-ad` backend route

**Files:**
- Modify: `app_with_ai.py` (after the music routes added in Task 1)

- [ ] **Step 1: Add the route**

Add directly after the `serve_music` function:

```python
@app.route('/generate-video-ad', methods=['POST'])
@login_required
def generate_video_ad():
    import subprocess, tempfile, textwrap

    # ── Parse inputs ──────────────────────────────────────────────────────────
    try:
        products  = json.loads(request.form.get('products', '[]'))
        style     = request.form.get('style', 'slideshow')          # slideshow | kenburns
        duration  = max(10, min(60, int(request.form.get('duration', 30))))
    except Exception as e:
        return jsonify({'error': f'Bad request: {e}'})

    # Determine music path
    music_file_upload = request.files.get('music_file')
    music_track_name  = request.form.get('music_track', '').strip()

    if not products:
        return jsonify({'error': 'No products provided.'})
    if not music_file_upload and not music_track_name:
        return jsonify({'error': 'No music track selected.'})

    # Check ffmpeg
    ffmpeg_path = '/usr/bin/ffmpeg'
    if not os.path.exists(ffmpeg_path):
        return jsonify({'error': 'Video generation not available on this server (ffmpeg missing).'})

    generated = []
    tmp_files = []   # track temp files to clean up

    try:
        # Save uploaded music to a temp file if provided
        if music_file_upload:
            if music_file_upload.content_length and music_file_upload.content_length > 20 * 1024 * 1024:
                return jsonify({'error': 'Uploaded MP3 must be under 20 MB.'})
            tmp_music = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            music_file_upload.save(tmp_music.name)
            tmp_files.append(tmp_music.name)
            music_path = tmp_music.name
        else:
            music_path = os.path.join(MUSIC_FOLDER, os.path.basename(music_track_name))
            if not os.path.exists(music_path):
                return jsonify({'error': f'Music track not found: {music_track_name}'})

        for p in products:
            sku   = p.get('sku', 'UNKNOWN')
            title = p.get('title', 'Untitled')
            price = p.get('price', '0.00')
            image_url = p.get('image', '')

            # ── Build frame image with Pillow ─────────────────────────────────
            from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
            import io as _io

            W, H = 1280, 720
            frame = _Img.new('RGB', (W, H), color=(26, 26, 46))

            # Paste product image if available
            if image_url:
                img_filename = image_url.split('/')[-1]
                img_path = os.path.join(UPLOAD_FOLDER, img_filename)
                if os.path.exists(img_path):
                    try:
                        prod_img = _Img.open(img_path).convert('RGB')
                        # Fit into left 860px, full height
                        prod_img.thumbnail((860, H), _Img.LANCZOS)
                        frame.paste(prod_img, (0, (H - prod_img.height) // 2))
                    except Exception:
                        pass

            # Dark right-side overlay for text
            overlay = _Img.new('RGBA', (W, H), (0, 0, 0, 0))
            _Draw.Draw(overlay).rectangle([(820, 0), (W, H)], fill=(15, 15, 35, 230))
            frame = _Img.alpha_composite(frame.convert('RGBA'), overlay).convert('RGB')

            draw = _Draw.Draw(frame)

            # Try to use a system font, fall back to default
            try:
                font_title = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 42)
                font_price = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 64)
                font_small = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 26)
            except Exception:
                font_title = _Font.load_default()
                font_price = font_title
                font_small = font_title

            # Wrap title to ~20 chars per line
            lines = textwrap.wrap(title, width=20)
            y = 180
            for line in lines[:3]:
                draw.text((850, y), line, font=font_title, fill=(240, 192, 64))
                y += 54

            # Price
            draw.text((850, y + 20), f'${price}', font=font_price, fill=(255, 255, 255))

            # Store name footer
            draw.text((850, H - 80), 'Liberty Emporium', font=font_small, fill=(160, 160, 200))
            draw.text((850, H - 48), '125 W Swannanoa Ave, Liberty NC', font=font_small, fill=(120, 120, 160))

            # Save frame to temp file
            tmp_frame = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            frame.save(tmp_frame.name, 'JPEG', quality=92)
            tmp_files.append(tmp_frame.name)

            # ── Run ffmpeg ────────────────────────────────────────────────────
            ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            out_name = f'video_ad_{sku}_{ts}.mp4'
            out_path = os.path.join(ADS_FOLDER, out_name)

            if style == 'kenburns':
                vf = f"zoompan=z='min(zoom+0.0015,1.5)':d={duration * 25}:s=1280x720,fps=25"
                cmd = [
                    ffmpeg_path, '-y',
                    '-loop', '1', '-i', tmp_frame.name,
                    '-i', music_path,
                    '-vf', vf,
                    '-c:v', 'libx264', '-c:a', 'aac',
                    '-t', str(duration),
                    '-pix_fmt', 'yuv420p',
                    '-shortest',
                    out_path
                ]
            else:  # slideshow (default)
                cmd = [
                    ffmpeg_path, '-y',
                    '-loop', '1', '-i', tmp_frame.name,
                    '-i', music_path,
                    '-c:v', 'libx264', '-tune', 'stillimage',
                    '-c:a', 'aac',
                    '-t', str(duration),
                    '-pix_fmt', 'yuv420p',
                    '-shortest',
                    out_path
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return jsonify({'error': f'ffmpeg failed: {result.stderr[-500:]}'})

            generated.append({'filename': out_name, 'product_title': title})

    finally:
        for f in tmp_files:
            try:
                os.unlink(f)
            except Exception:
                pass

    return jsonify({'success': True, 'files': generated})
```

- [ ] **Step 2: Make sure `json` is imported at the top of app_with_ai.py**

Search for `import json` in `app_with_ai.py`. If it's not there, add it near the top with the other imports.

- [ ] **Step 3: Manually test the route exists**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
python -c "import app_with_ai; print('Import OK')"
```
Expected output: `Import OK` (no tracebacks).

- [ ] **Step 4: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add POST /generate-video-ad route with ffmpeg + Pillow composition"
```

---

## Task 3: Add Video Studio panel HTML + CSS to ad_generator.html

**Files:**
- Modify: `templetes/ad_generator.html`

- [ ] **Step 1: Add CSS for the Video Studio panel**

In `ad_generator.html`, find the closing `</style>` tag just before `{% endblock %}` of `{% block content %}` (around the `.max-warning` section). Add before `</style>`:

```css
/* ── Video Studio Panel ───────────────────────────────────────── */
.vs-panel {
  margin: 2rem 0;
  border: 2px solid #7c3aed;
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(124,58,237,0.18);
  font-family: 'DM Sans', sans-serif;
}

.vs-header {
  background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%);
  color: white;
  padding: 1rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.vs-header h3 { margin: 0; font-size: 1.1rem; font-weight: 800; }
.vs-header p  { margin: 0.2rem 0 0; font-size: 0.8rem; opacity: 0.85; }

.vs-badge {
  background: rgba(255,255,255,0.2);
  border-radius: 20px;
  padding: 0.2rem 0.7rem;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.vs-body { background: white; padding: 1.25rem 1.5rem; }

.vs-section-label {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: #666;
  margin-bottom: 0.6rem;
}

/* Video style cards */
.vs-style-grid {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}

.vs-style-card {
  flex: 1;
  border: 2px solid #e0ddd8;
  border-radius: 10px;
  padding: 0.75rem 1rem;
  cursor: pointer;
  text-align: center;
  transition: all 0.2s;
}

.vs-style-card:hover { border-color: #7c3aed; }
.vs-style-card.active {
  border-color: #7c3aed;
  background: #f5f3ff;
}

.vs-style-card .vs-card-icon { font-size: 1.5rem; margin-bottom: 0.25rem; }
.vs-style-card h4 { font-size: 0.88rem; font-weight: 700; color: #333; margin: 0 0 0.2rem; }
.vs-style-card p  { font-size: 0.75rem; color: #888; margin: 0; }
.vs-style-card.active h4 { color: #7c3aed; }

/* Music track grid */
.vs-music-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 0.6rem;
  margin-bottom: 0.75rem;
}

.vs-track-card {
  border: 2px solid #e0ddd8;
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  transition: all 0.2s;
}

.vs-track-card:hover { border-color: #7c3aed; }
.vs-track-card.active {
  border-color: #7c3aed;
  background: #f5f3ff;
}

.vs-track-card .vs-track-name {
  font-size: 0.82rem;
  font-weight: 700;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.vs-track-card.active .vs-track-name { color: #7c3aed; }

.vs-play-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  padding: 0;
  flex-shrink: 0;
  line-height: 1;
}

.vs-upload-zone {
  border: 2px dashed #7c3aed;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
  background: #faf8ff;
  transition: background 0.2s;
  margin-bottom: 1.25rem;
}

.vs-upload-zone:hover { background: #f0ecff; }
.vs-upload-zone .vs-upload-label { font-size: 0.85rem; font-weight: 700; color: #7c3aed; }
.vs-upload-zone .vs-upload-sub   { font-size: 0.75rem; color: #999; }

/* Duration slider */
.vs-slider-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}

.vs-slider-row span { font-size: 0.78rem; color: #999; white-space: nowrap; }
.vs-slider-row input[type=range] { flex: 1; accent-color: #7c3aed; }

/* Generate video button */
.btn-generate-video {
  width: 100%;
  background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%);
  color: white;
  border: none;
  padding: 0.9rem;
  border-radius: 10px;
  font-size: 1rem;
  font-weight: 800;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  transition: opacity 0.2s;
}

.btn-generate-video:hover:not(:disabled) { opacity: 0.88; }
.btn-generate-video:disabled { opacity: 0.4; cursor: not-allowed; }

/* Video results */
.vs-results {
  margin-top: 2rem;
  padding-top: 2rem;
  border-top: 2px solid #ede9fe;
  display: none;
}

.vs-results.visible { display: block; }

.vs-results h2 {
  font-family: 'Playfair Display', serif;
  font-size: 1.6rem;
  color: #4f46e5;
  margin-bottom: 1.25rem;
}

.vs-video-card {
  background: white;
  border: 1px solid #ede9fe;
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 1.5rem;
  box-shadow: 0 2px 12px rgba(124,58,237,0.1);
}

.vs-video-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: #faf8ff;
  border-bottom: 1px solid #ede9fe;
  gap: 0.75rem;
}

.vs-video-title { font-weight: 700; font-size: 0.95rem; color: #333; }

.btn-dl-video {
  background: #7c3aed;
  color: white;
  padding: 0.45rem 1rem;
  border-radius: 6px;
  text-decoration: none;
  font-size: 0.85rem;
  font-weight: 700;
  white-space: nowrap;
}

.btn-dl-video:hover { background: #6d28d9; }
```

- [ ] **Step 2: Commit CSS only first**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
git add templetes/ad_generator.html
git commit -m "feat: add Video Studio CSS to ad_generator.html"
```

---

## Task 4: Add Video Studio panel HTML to ad_generator.html

**Files:**
- Modify: `templetes/ad_generator.html`

- [ ] **Step 1: Insert the Video Studio panel HTML**

In `ad_generator.html`, find this block (around line 570):
```html
<!-- Generated Ads Section (shown after generation) -->
<div class="generated-ads-section" id="generatedSection">
```

Insert the following **before** that line:

```html
<!-- ══ Video Studio Panel ══ -->
<div class="vs-panel">
  <div class="vs-header">
    <div>
      <h3>🎬 Video Studio</h3>
      <p>Turn your product photos into video ads with music</p>
    </div>
    <span class="vs-badge">NEW</span>
  </div>
  <div class="vs-body">

    <!-- Video Style -->
    <div class="vs-section-label">🎨 Video Style</div>
    <div class="vs-style-grid">
      <div class="vs-style-card active" data-vstyle="slideshow" onclick="vsSelectStyle(this)">
        <div class="vs-card-icon">🎞️</div>
        <h4>Slideshow</h4>
        <p>Smooth fade &amp; slide</p>
      </div>
      <div class="vs-style-card" data-vstyle="kenburns" onclick="vsSelectStyle(this)">
        <div class="vs-card-icon">🎥</div>
        <h4>Ken Burns</h4>
        <p>Slow cinematic zoom</p>
      </div>
    </div>

    <!-- Music Track -->
    <div class="vs-section-label">🎵 Music Track</div>
    <div class="vs-music-grid" id="vsMusicGrid">
      <!-- Populated by JS from GET /music -->
      <div style="color:#aaa; font-size:0.88rem; padding:0.5rem;">Loading tracks...</div>
    </div>

    <!-- Upload own track -->
    <div class="vs-upload-zone" onclick="document.getElementById('vsUploadInput').click()">
      <span style="font-size:1.3rem;">📤</span>
      <div>
        <div class="vs-upload-label" id="vsUploadLabel">Upload Your Own Track</div>
        <div class="vs-upload-sub">MP3 files only · max 20 MB</div>
      </div>
      <input type="file" id="vsUploadInput" accept=".mp3" style="display:none" onchange="vsHandleUpload(this)">
    </div>

    <!-- Duration -->
    <div class="vs-section-label">⏱️ Duration — <span id="vsDurationLabel" style="color:#7c3aed; font-weight:800;">30 seconds</span></div>
    <div class="vs-slider-row">
      <span>10s</span>
      <input type="range" id="vsDurationSlider" min="10" max="60" value="30"
             oninput="document.getElementById('vsDurationLabel').textContent = this.value + ' seconds'">
      <span>60s</span>
    </div>

    <!-- Generate button -->
    <button class="btn-generate-video" id="vsGenerateBtn" disabled onclick="vsGenerate()">
      🎬 Create Video Ad
    </button>
    <div id="vsError" style="color:#e53e3e; font-size:0.85rem; margin-top:0.6rem; display:none;"></div>

  </div>
</div>

<!-- Video Results -->
<div class="vs-results" id="vsResults">
  <h2>🎬 Generated Video Ads</h2>
  <div id="vsResultsList"></div>
</div>
```

- [ ] **Step 2: Verify the page loads without errors**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
python -c "
from app_with_ai import app
with app.test_client() as c:
    rv = c.get('/ads')
    print('Status:', rv.status_code)  # expect 302 (redirect to login)
"
```
Expected: `Status: 302`

- [ ] **Step 3: Commit**

```bash
git add templetes/ad_generator.html
git commit -m "feat: add Video Studio panel HTML to ad_generator.html"
```

---

## Task 5: Add Video Studio JavaScript to ad_generator.html

**Files:**
- Modify: `templetes/ad_generator.html`

- [ ] **Step 1: Add VS JavaScript before the closing `</script>` tag**

In `ad_generator.html`, find the closing `</script>` tag near the bottom of the `{% block content %}` block (after the `showGeneratedAds` function). Insert the following **before** `</script>`:

```javascript
// ── Video Studio ──────────────────────────────────────────────────────────────
let vsSelectedStyle  = 'slideshow';
let vsSelectedTrack  = null;   // filename string (library) or null
let vsUploadedFile   = null;   // File object (upload) or null
let vsAudioPlayer    = null;   // current HTMLAudioElement for preview

function vsSelectStyle(card) {
  document.querySelectorAll('.vs-style-card').forEach(c => c.classList.remove('active'));
  card.classList.add('active');
  vsSelectedStyle = card.dataset.vstyle;
}

function vsSelectTrack(card, filename) {
  // Deselect all library cards
  document.querySelectorAll('.vs-track-card').forEach(c => c.classList.remove('active'));
  card.classList.add('active');
  vsSelectedTrack = filename;
  vsUploadedFile  = null;
  document.getElementById('vsUploadLabel').textContent = 'Upload Your Own Track';
  vsUpdateGenerateBtn();
}

function vsHandleUpload(input) {
  const file = input.files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    vsShowError('Uploaded MP3 must be under 20 MB.');
    input.value = '';
    return;
  }
  vsUploadedFile  = file;
  vsSelectedTrack = null;
  document.querySelectorAll('.vs-track-card').forEach(c => c.classList.remove('active'));
  document.getElementById('vsUploadLabel').textContent = '✅ ' + file.name;
  vsUpdateGenerateBtn();
}

function vsPlayPreview(btn, filename) {
  // Stop any existing preview
  if (vsAudioPlayer) { vsAudioPlayer.pause(); vsAudioPlayer = null; }
  const icon = btn.querySelector('.vs-play-icon');
  if (icon && icon.textContent === '⏸️') { icon.textContent = '▶️'; return; }
  document.querySelectorAll('.vs-play-icon').forEach(i => i.textContent = '▶️');
  vsAudioPlayer = new Audio('/music/' + encodeURIComponent(filename));
  vsAudioPlayer.play();
  if (icon) icon.textContent = '⏸️';
  vsAudioPlayer.onended = () => { if (icon) icon.textContent = '▶️'; };
}

function vsUpdateGenerateBtn() {
  const hasProduct = selectedSkus.size > 0;
  const hasMusic   = vsSelectedTrack !== null || vsUploadedFile !== null;
  document.getElementById('vsGenerateBtn').disabled = !(hasProduct && hasMusic);
}

function vsShowError(msg) {
  const el = document.getElementById('vsError');
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

function vsGenerate() {
  vsShowError('');
  if (selectedSkus.size === 0) { vsShowError('Select at least one product above.'); return; }
  if (!vsSelectedTrack && !vsUploadedFile) { vsShowError('Select or upload a music track.'); return; }

  const products = [];
  document.querySelectorAll('.selectable-card.selected').forEach(card => {
    products.push({
      sku: card.dataset.sku, title: card.dataset.title,
      price: card.dataset.price, image: card.dataset.image,
      description: card.dataset.description
    });
  });

  const duration = document.getElementById('vsDurationSlider').value;
  const formData = new FormData();
  formData.append('products',  JSON.stringify(products));
  formData.append('style',     vsSelectedStyle);
  formData.append('duration',  duration);
  if (vsUploadedFile) {
    formData.append('music_file', vsUploadedFile);
  } else {
    formData.append('music_track', vsSelectedTrack);
  }

  const btn = document.getElementById('vsGenerateBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-ring" style="width:22px;height:22px;border-width:3px;border-top-color:white;display:inline-block;"></span> &nbsp;Generating…';
  document.getElementById('loadingOverlay').classList.add('active');
  document.querySelector('#loadingOverlay div:last-child').textContent = 'Creating video ad… this may take up to a minute.';

  fetch('/generate-video-ad', { method: 'POST', body: formData })
  .then(r => r.json())
  .then(data => {
    document.getElementById('loadingOverlay').classList.remove('active');
    btn.disabled = false;
    btn.innerHTML = '🎬 Create Video Ad';
    if (data.error) { vsShowError(data.error); return; }
    vsShowResults(data.files);
  })
  .catch(e => {
    document.getElementById('loadingOverlay').classList.remove('active');
    btn.disabled = false;
    btn.innerHTML = '🎬 Create Video Ad';
    vsShowError('Network error: ' + e);
  });
}

function vsShowResults(files) {
  const list = document.getElementById('vsResultsList');
  list.innerHTML = '';
  files.forEach(f => {
    const card = document.createElement('div');
    card.className = 'vs-video-card';
    card.innerHTML = `
      <div class="vs-video-card-header">
        <span class="vs-video-title">🎬 ${f.product_title}</span>
        <a href="/download-ad/${f.filename}" class="btn-dl-video">⬇️ Download MP4</a>
      </div>
      <video src="/ads/${f.filename}" controls autoplay muted loop
             style="width:100%; display:block; max-height:480px; background:#000;"></video>
    `;
    list.appendChild(card);
  });
  const results = document.getElementById('vsResults');
  results.classList.add('visible');
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Load music library on page load
function vsLoadMusicLibrary() {
  fetch('/music')
  .then(r => r.json())
  .then(tracks => {
    const grid = document.getElementById('vsMusicGrid');
    if (!tracks.length) { grid.innerHTML = '<div style="color:#aaa;font-size:0.88rem;">No tracks in library yet.</div>'; return; }
    grid.innerHTML = '';
    tracks.forEach(t => {
      const card = document.createElement('div');
      card.className = 'vs-track-card';
      card.innerHTML = `
        <button class="vs-play-btn" onclick="event.stopPropagation(); vsPlayPreview(this, '${t.filename}')">
          <span class="vs-play-icon">▶️</span>
        </button>
        <div class="vs-track-name" title="${t.filename}">${t.display_name}</div>
      `;
      card.onclick = () => vsSelectTrack(card, t.filename);
      grid.appendChild(card);
    });
  })
  .catch(() => {
    document.getElementById('vsMusicGrid').innerHTML = '<div style="color:#e53e3e;font-size:0.88rem;">Could not load music library.</div>';
  });
}

// Re-evaluate generate button when product selection changes (hook into existing updateUI)
const _origUpdateUI = updateUI;
updateUI = function() { _origUpdateUI(); vsUpdateGenerateBtn(); };

document.addEventListener('DOMContentLoaded', vsLoadMusicLibrary);
```

- [ ] **Step 2: Verify JS syntax is clean**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
python -c "
from app_with_ai import app
with app.test_client() as c:
    rv = c.get('/ads')
    print('Route OK, status:', rv.status_code)
"
```
Expected: `Route OK, status: 302`

- [ ] **Step 3: Commit**

```bash
git add templetes/ad_generator.html
git commit -m "feat: add Video Studio JavaScript — music library loader, style/track pickers, video generation"
```

---

## Task 6: End-to-end smoke test + push to GitHub

**Files:** None — testing and push only.

- [ ] **Step 1: Manual end-to-end test checklist**

Log in to the app locally or on Railway and verify each item:

```
[ ] Ad Generator page loads without JS errors (check browser console)
[ ] Video Studio panel appears below the sticky generate bar
[ ] Music library cards load (4 tracks from music/ folder visible)
[ ] Clicking a track card highlights it purple
[ ] ▶️ play button previews audio in browser; ⏸️ pauses it
[ ] Upload zone accepts an MP3 file and shows filename
[ ] Duration slider label updates live as you drag
[ ] "Create Video Ad" button is disabled until a product AND music are selected
[ ] Selecting a product + track enables the button
[ ] Clicking the button shows the loading overlay with updated message
[ ] A video card appears in the results with a working <video> player
[ ] Download MP4 button downloads the file
[ ] Ken Burns style produces a zooming video (verify visually)
[ ] Slideshow style produces a static-frame video (verify visually)
[ ] Uploading own MP3 instead of library track works correctly
```

- [ ] **Step 2: Update the How-To slides for the Ad Generator page**

In `templetes/ad_generator.html`, find the `{% block howto_slides %}` block and add two new slides at the end (before `{% endblock %}`):

```html
<div class="ht-slide">
  <div class="ht-icon">🎬</div>
  <div class="ht-label">Video Studio</div>
  <h2>Creating a Video Ad</h2>
  <p>The purple <strong>🎬 Video Studio</strong> panel below the generate bar turns your product photos into MP4 video ads with music. Use the same selected products as image ads.</p>
  <ol>
    <li>Pick a <strong>Video Style</strong> — Slideshow or Ken Burns zoom.</li>
    <li>Select a <strong>Music Track</strong> from the library (click ▶️ to preview) or upload your own MP3.</li>
    <li>Set the <strong>Duration</strong> with the slider (10–60 seconds).</li>
    <li>Click <strong>"🎬 Create Video Ad"</strong> — takes up to a minute.</li>
  </ol>
</div>
<div class="ht-slide">
  <div class="ht-icon">⬇️</div>
  <div class="ht-label">Video Results</div>
  <h2>Your Generated Videos</h2>
  <p>Each video appears below the Video Studio panel with an inline preview player and a <strong>⬇️ Download MP4</strong> button. Videos are saved on the server so you can download them any time.</p>
  <div class="ht-tip">Ken Burns videos may take slightly longer to generate than Slideshow videos due to the zoom animation processing.</div>
</div>
```

- [ ] **Step 3: Commit how-to update**

```bash
git add templetes/ad_generator.html
git commit -m "docs: update Ad Generator how-to slides with Video Studio instructions"
```

- [ ] **Step 4: Push everything to GitHub**

```bash
git push origin main
```

Expected: push succeeds, Railway auto-deploys.
