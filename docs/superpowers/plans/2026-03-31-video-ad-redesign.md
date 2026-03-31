# Video Ad Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the split-panel video ad layout with a full-bleed product photo, dark gradient overlay, bigger text hierarchy, and a text fade-in animation via FFmpeg.

**Architecture:** A new `_draw_text_layer()` helper renders all text onto a transparent RGBA PNG. The `generate_video_ad()` function builds a background frame (photo + gradient, no text) saved as `bg.jpg`, then calls the helper to produce `text.png`. FFmpeg receives both images as separate inputs and uses `overlay` + `fade` to animate the text in at t=1s.

**Tech Stack:** Python/Flask, Pillow (PIL), FFmpeg, subprocess

---

### Task 1: Add `_draw_text_layer()` helper

**Files:**
- Modify: `app_with_ai.py` — insert before line 673 (`@app.route('/generate-video-ad', ...)`)

- [ ] **Step 1: Insert helper function**

Add immediately before the `@app.route('/generate-video-ad', ...)` decorator:

```python
def _draw_text_layer(W, H, store_name, title, price, description, cta_text, tagline,
                     font_bold_path, font_reg_path, template_config):
    """Return a transparent RGBA PIL Image with all text for a full-bleed ad."""
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
    import textwrap as _tw

    layer = _Img.new('RGBA', (W, H), (0, 0, 0, 0))
    draw  = _Draw.Draw(layer)

    sz_store = max(18, int(W * 0.016))
    sz_title = max(48, int(W * 0.048))
    sz_price = max(64, int(W * 0.072))
    sz_desc  = max(20, int(W * 0.020))
    sz_cta   = max(24, int(W * 0.026))

    try:
        f_store = _Font.truetype(font_reg_path,  sz_store)
        f_title = _Font.truetype(font_bold_path, sz_title)
        f_price = _Font.truetype(font_bold_path, sz_price)
        f_desc  = _Font.truetype(font_reg_path,  sz_desc)
        f_cta   = _Font.truetype(font_bold_path, sz_cta)
    except Exception:
        default = _Font.load_default()
        f_store = f_title = f_price = f_desc = f_cta = default

    def _hex(h, a=255):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (a,)

    accent = _hex(template_config['accent'])
    white  = (255, 255, 255, 255)
    dimmed = (160, 160, 200, 255)
    desc_c = (200, 200, 220, 255)
    tag_c  = (120, 120, 160, 255)

    grad_top = int(H * 0.60)
    x = int(W * 0.05)
    y = grad_top + int(H * 0.025)

    draw.text((x, y), store_name, font=f_store, fill=dimmed)
    y += sz_store + max(6, int(sz_store * 0.5))

    chars_t = max(10, int((W * 0.9) / (sz_title * 0.58)))
    for line in _tw.wrap(title, width=chars_t)[:2]:
        draw.text((x, y), line, font=f_title, fill=accent)
        y += sz_title + max(4, int(sz_title * 0.08))
    y += max(4, int(sz_title * 0.12))

    draw.text((x, y), f'${price}', font=f_price, fill=white)
    y += sz_price + max(8, int(sz_price * 0.15))

    if description:
        chars_d = max(15, int((W * 0.9) / (sz_desc * 0.58)))
        for line in _tw.wrap(description, width=chars_d)[:2]:
            draw.text((x, y), line, font=f_desc, fill=desc_c)
            y += sz_desc + max(4, int(sz_desc * 0.2))
        y += max(4, int(sz_desc * 0.3))

    if cta_text:
        chars_c = max(10, int((W * 0.9) / (sz_cta * 0.58)))
        for line in _tw.wrap(cta_text, width=chars_c)[:2]:
            draw.text((x, y), line, font=f_cta, fill=accent)
            y += sz_cta + max(4, int(sz_cta * 0.2))

    if tagline:
        ty = H - sz_store - int(H * 0.025)
        draw.text((x, ty), tagline, font=f_store, fill=tag_c)

    return layer
```

- [ ] **Step 2: Verify syntax**

```bash
cd "/home/mingo/Documents/Finished APPs for Demos/Inventory Demo"
python -c "import ast; ast.parse(open('app_with_ai.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add _draw_text_layer() helper for full-bleed ad text"
```

---

### Task 2: Rewrite frame-building in `generate_video_ad()`

**Files:**
- Modify: `app_with_ai.py` — replace old frame build block (lines ~771–920) with full-bleed + gradient

- [ ] **Step 1: Replace old frame-building block**

Replace the entire block from `# ── Build frame image with Pillow ─────────────────────────────────` through `frame.save(tmp_frame.name, 'JPEG', quality=92)` with:

```python
            # ── Build frame image with Pillow ─────────────────────────────────
            from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
            import io as _io

            def _hex_rgb(h):
                h = h.lstrip('#')
                return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

            bg_dark_rgb = _hex_rgb(template_config['bg_dark'])

            # Start with solid bg_dark fill
            bg_frame = _Img.new('RGB', (W, H), color=bg_dark_rgb)

            # Cover-crop product photo to fill entire frame
            if image_url:
                img_filename = image_url.split('/')[-1]
                img_path = os.path.join(UPLOAD_FOLDER, img_filename)
                if os.path.exists(img_path):
                    try:
                        prod_img = _Img.open(img_path)
                        prod_img = fix_image_orientation(prod_img)
                        prod_img = prod_img.convert('RGB')
                        scale = max(W / prod_img.width, H / prod_img.height)
                        new_w = int(prod_img.width * scale)
                        new_h = int(prod_img.height * scale)
                        prod_img = prod_img.resize((new_w, new_h), _Img.LANCZOS)
                        cx = (new_w - W) // 2
                        cy = (new_h - H) // 2
                        prod_img = prod_img.crop((cx, cy, cx + W, cy + H))
                        bg_frame.paste(prod_img, (0, 0))
                    except Exception:
                        pass

            # Dark gradient: bottom 40%, opaque at bottom → transparent at top
            grad_h = int(H * 0.40)
            grad_y = H - grad_h
            grad_img = _Img.new('RGBA', (W, grad_h), (0, 0, 0, 0))
            grad_draw = _Draw.Draw(grad_img)
            for row in range(grad_h):
                alpha = int(220 * row / grad_h)
                grad_draw.line([(0, row), (W - 1, row)], fill=(*bg_dark_rgb, alpha))
            bg_frame = bg_frame.convert('RGBA')
            bg_frame.paste(grad_img, (0, grad_y), grad_img)
            bg_frame = bg_frame.convert('RGB')

            # Logo on background layer (optional)
            if logo_path and os.path.exists(logo_path):
                try:
                    logo_img = _Img.open(logo_path)
                    logo_img = fix_image_orientation(logo_img)
                    logo_img = logo_img.convert('RGBA')
                    size_map = {'small': 60, 'medium': 90, 'large': 120}
                    target_size = int(size_map.get(logo_size, 90) * (W / 1280.0))
                    logo_img.thumbnail((target_size, target_size), _Img.LANCZOS)
                    padding = int(W * 0.015)
                    pos_map = {
                        'top-left':     (padding, padding),
                        'top-right':    (W - logo_img.width - padding, padding),
                        'bottom-left':  (padding, H - logo_img.height - padding),
                        'bottom-right': (W - logo_img.width - padding, H - logo_img.height - padding),
                    }
                    pos = pos_map.get(logo_position, pos_map['top-right'])
                    bg_frame = bg_frame.convert('RGBA')
                    bg_frame.paste(logo_img, pos, logo_img)
                    bg_frame = bg_frame.convert('RGB')
                except Exception:
                    pass

            # Text layer: transparent RGBA PNG, text only
            font_bold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            font_reg  = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
            text_layer = _draw_text_layer(
                W, H,
                store_name='Liberty Emporium',
                title=title, price=price,
                description=description,
                cta_text=cta_text,
                tagline=tagline,
                font_bold_path=font_bold,
                font_reg_path=font_reg,
                template_config=template_config,
            )

            # Save bg.jpg and text.png
            tmp_bg   = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp_text = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            bg_frame.save(tmp_bg.name,    'JPEG', quality=92)
            text_layer.save(tmp_text.name, 'PNG')
            tmp_files.extend([tmp_bg.name, tmp_text.name])
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('app_with_ai.py').read()); print('OK')"
```
Expected: `OK`

---

### Task 3: Update FFmpeg commands + fix return bug

**Files:**
- Modify: `app_with_ai.py` — replace old ffmpeg block and fix return placement

- [ ] **Step 1: Replace ffmpeg block and fix return**

Replace the old `# ── Run ffmpeg ────────` block (from `ts = datetime.datetime...` through `return jsonify({'success': True, 'files': generated})`) with:

```python
            # ── Run ffmpeg ────────────────────────────────────────────────────
            ts           = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            template_sfx = f'_{template}' if template != 'default' else ''
            format_sfx   = f'_{format_str}' if format_str != '1920x1080' else ''
            out_name     = f'video_ad_{sku}{template_sfx}{format_sfx}_{ts}.mp4'
            out_path     = os.path.join(ADS_FOLDER, out_name)

            # Text fades in: invisible 0–1s, fades over 0.8s
            if style == 'kenburns':
                zoom_d = duration * 25
                fc = (
                    f"[0:v]zoompan=z='min(zoom+0.0015,1.5)':d={zoom_d}:s={W}x{H},fps=25[bg];"
                    f"[1:v]fade=in:st=1:d=0.8:alpha=1[txt];"
                    f"[bg][txt]overlay=0:0[outv]"
                )
            else:  # slideshow
                fc = (
                    "[1:v]fade=in:st=1:d=0.8:alpha=1[txt];"
                    "[0:v][txt]overlay=0:0[outv]"
                )

            cmd = [
                ffmpeg_path, '-y',
                '-loop', '1', '-framerate', '25', '-i', tmp_bg.name,
                '-loop', '1', '-framerate', '25', '-i', tmp_text.name,
                '-i', music_path,
                '-filter_complex', fc,
                '-map', '[outv]', '-map', '2:a',
                '-c:v', 'libx264', '-c:a', 'aac',
                '-t', str(duration),
                '-pix_fmt', 'yuv420p',
                '-shortest',
                out_path,
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Video generation timed out (try a shorter duration or smaller format).'})
            if result.returncode != 0:
                return jsonify({'error': f'ffmpeg failed: {result.stderr[-500:]}'})

            generated.append({'filename': out_name, 'product_title': title})

        # ── Return all generated files (outside for loop) ──────────────────────
        return jsonify({'success': True, 'files': generated})
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('app_with_ai.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: full-bleed ad layout, bigger text, text fade-in via ffmpeg overlay"
```
