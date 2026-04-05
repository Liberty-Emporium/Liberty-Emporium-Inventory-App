#!/usr/bin/env python3
"""
test_video_generation.py — Comprehensive test suite for the video ad generation pipeline.
Tests every path BEFORE deploying to Railway.
Run: python3 test_video_generation.py
"""
import subprocess, os, sys, tempfile, json

PASS = FAIL = 0
WORKDIR = None

def run(cmd, timeout=15):
    """Run subprocess, return (rc, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stderr[-300:] if r.stderr else "")

def result(name, ok, msg=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name} {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {msg or '(no detail)'}")

def test_zoompan(direction_name, expr, w=320, h=240):
    """Test a single zoompan expression on a color input."""
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', f'color=c=blue:s={w}x{h}:rate=25:d=0.5',
        '-vf', f"zoompan={expr}:d=12:s={w}x{h}:fps=25",
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        f'{WORKDIR}/zp_{direction_name}.mp4'
    ]
    rc, err = run(cmd, timeout=30)
    of = f'{WORKDIR}/zp_{direction_name}.mp4'
    ok = rc == 0 and os.path.exists(of) and os.path.getsize(of) > 500
    msg = f"{os.path.getsize(of)//1024}KB" if ok else err.strip()[:150]
    result(f"Zoompan: {direction_name}", ok, msg)

def test_full_pipeline(num_products=2, style='kenburns', w=640, h=480):
    """Test a full pipeline matching the actual app code."""
    fps = 25
    t_per = 3.0
    intro_dur = 2.5
    outro_dur = 3.0
    crossfade = 0.8
    total_dur = intro_dur + num_products * t_per + outro_dur
    
    directions = [
        ("z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("z='min(zoom+0.003,1.3)':x='if(eq(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)'"),
        ("z='min(zoom+0.003,1.3)':x='if(eq(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)'"),
    ]
    
    # Generate products + overlays + intro/outro using color inputs (fastest)
    # We'll use -f lavfi color for intro and outro, and loop product jpegs
    colors = ['navy', 'red', 'green', 'gold', 'teal']
    
    intro_path = f'{WORKDIR}/intro.jpg'
    outro_path = f'{WORKDIR}/outro.jpg'
    product_paths = []
    overlay_paths = []
    
    # Create intro/outro JPEGs
    run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
         f'color=c=darkblue:s={w}x{h}:d=0.04:r=1', '-frames:v', '1', '-update', '1', intro_path], timeout=10)
    run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
         f'color=c=gold:s={w}x{h}:d=0.04:r=1', '-frames:v', '1', '-update', '1', outro_path], timeout=10)
    
    # Create product JPEGs and transparent PNG overlays
    for i in range(num_products):
        pp = f'{WORKDIR}/prod{i}.jpg'
        op = f'{WORKDIR}/overlay{i}.png'
        run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
             f'color=c={colors[i % len(colors)]}:s={w}x{h}:d=0.04:r=1',
             '-frames:v', '1', '-update', '1', pp], timeout=10)
        run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
             f'color=c=black@0.0:s={w}x{h}:d=0.04:r=1',
             '-frames:v', '1', '-update', '1', op], timeout=10)
        product_paths.append(pp)
        overlay_paths.append(op)
    
    # Create audio
    audio_path = f'{WORKDIR}/audio.mp3'
    audio_dur = int(total_dur + 5)
    run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
         f'sine=frequency=440:duration={audio_dur}:sample_rate=44100',
         '-b:a', '128k', audio_path], timeout=15)
    
    # Build ffmpeg command
    cmd = ['ffmpeg', '-y', '-loglevel', 'warning']
    
    # Input 0: intro
    cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(intro_dur), '-i', intro_path]
    
    # Inputs 1..n: products & overlays
    for i in range(num_products):
        cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(t_per), '-i', product_paths[i]]
        cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(t_per), '-i', overlay_paths[i]]
    
    # Input (1 + 2*n): outro — use color source for speed
    cmd += ['-f', 'lavfi', '-i', f'color=c=gold:s={w}x{h}:d={outro_dur}:r={fps}']
    
    # Input (2 + 2*n): audio
    audio_idx = 2 + 2 * num_products
    cmd += ['-i', audio_path]
    
    # Build filter_complex
    parts = []
    parts.append(f"[0:v]fps={fps}[intro]")
    
    for i in range(num_products):
        bg_idx = 1 + 2*i
        tx_idx = 1 + 2*i + 1
        
        if style == 'kenburns' and num_products <= 4:
            # Zoompan is slow for many products — only use for small sets
            dur_frames = int(t_per * fps)
            zoom_expr = directions[i % len(directions)]
            parts.append(f"[{bg_idx}:v]zoompan={zoom_expr}:d={dur_frames}:s={w}x{h}:fps={fps}[kb{i}]")
            bg_ref = f"kb{i}"
        else:
            parts.append(f"[{bg_idx}:v]fps={fps}[bg{i}]")
            bg_ref = f"bg{i}"
        
        parts.append(f"[{tx_idx}:v]fade=in:st=0:d=0.8:alpha=1[tx{i}]")
        parts.append(f"[{bg_ref}][tx{i}]overlay=0:0[prod{i}]")
    
    outro_idx = audio_idx - 1
    parts.append(f"[{outro_idx}:v]fps={fps}[outro]")
    
    # Chain with xfade
    parts.append(f"[intro][prod0]xfade=transition=fade:duration={crossfade}:offset={intro_dur - crossfade}[chain0]")
    for i in range(1, num_products):
        prev = f"chain{i-1}"
        offset = intro_dur + sum(t_per for _ in range(i)) - crossfade
        parts.append(f"[{prev}][prod{i}]xfade=transition=fade:duration={crossfade}:offset={offset:.3f}[chain{i}]")
    
    last = f"chain{num_products-1}" if num_products > 1 else "chain0"
    outro_offset = intro_dur + num_products * t_per - crossfade
    parts.append(f"[{last}][outro]xfade=transition=fade:duration={crossfade}:offset={outro_offset:.3f}[outv]")
    
    parts.append(f"[outv]drawbox=x=0:y={h-6}:w=iw*t/{total_dur:.1f}:h=4:color=0xf0c040@0.95:t=fill[final]")
    
    fc = ';'.join(parts)
    
    out_file = f'{WORKDIR}/full_{style}_{num_products}prod.mp4'
    
    full_cmd = cmd + [
        '-filter_complex', fc,
        '-map', '[final]',
        '-map', f'{audio_idx}:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        '-shortest', '-t', str(total_dur),
        out_file
    ]
    
    rc, err = run(full_cmd, timeout=300)
    
    has_file = os.path.exists(out_file) and os.path.getsize(out_file) > 1000
    has_audio = False
    if has_file:
        ar, aout = run(['ffprobe', '-v', 'error', '-select_streams', 'a',
                        '-show_entries', 'stream=codec_type', '-of', 'csv', out_file], timeout=10)
        has_audio = 'audio' in aout
    
    size_kb = os.path.getsize(out_file) // 1024 if has_file else 0
    msg = f"{size_kb}KB, {'✓ audio' if has_audio else '✗ NO AUDIO'}"
    if not has_file:
        msg = err.strip()[:200]
    
    result(f"Full pipeline ({style}, {num_products} products)",
           rc == 0 and has_file and has_audio, msg)
    
    return has_file and has_audio

def main():
    global WORKDIR
    WORKDIR = tempfile.mkdtemp(prefix='vtest_')
    
    print(f"\n{'='*70}")
    print(f"🎬 VIDEO GENERATION TEST SUITE — Liberty Emporium Ad Generator")
    print(f"{'='*70}")
    print(f"Working directory: {WORKDIR}\n")
    
    # ─── Phase 1: Individual zoompan expressions ───
    print("Phase 1: Ken Burns zoompan expressions")
    print("-" * 50)
    
    directions = [
        ("zoom_in",  "z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("zoom_out", "z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("pan_rtol", "z='min(zoom+0.003,1.3)':x='if(eq(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)'"),
        ("pan_ltor", "z='min(zoom+0.003,1.3)':x='if(eq(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)'"),
    ]
    
    for name, expr in directions:
        test_zoompan(name, expr)
    
    # ─── Phase 2: Full pipeline tests ───
    print(f"\nPhase 2: Full video pipeline with ffmpeg")
    print("-" * 50)
    
    test_full_pipeline(num_products=2, style='kenburns')
    test_full_pipeline(num_products=2, style='slideshow')
    test_full_pipeline(num_products=3, style='kenburns')
    test_full_pipeline(num_products=4, style='kenburns')
    test_full_pipeline(num_products=1, style='kenburns')
    
    # ─── Phase 3: Audio integration ───
    print(f"\nPhase 3: Audio-only edge cases")
    print("-" * 50)
    
    # Test that audio file is properly generated
    audio_path = f'{WORKDIR}/audio.mp3'
    if os.path.exists(audio_path):
        ar, aout = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size',
                        '-of', 'json', audio_path], timeout=10)
        try:
            info = json.loads(aout)
            dur = info['format']['duration']
            sz = info['format']['size']
            result("Audio file valid", True, f"{dur}s, {int(sz)//1024}KB")
        except:
            result("Audio file valid", False, f"Cannot parse ffprobe output")
    else:
        result("Audio file exists", False, "File not found")
    
    # ─── Summary ───
    print(f"\n{'='*70}")
    print(f"📊 RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    if FAIL == 0:
        print("🎉 ALL TESTS PASSED — Safe to deploy to Railway!")
    else:
        print(f"⚠️  {FAIL} TESTS FAILED — Fix before deploying")
    print(f"📁 All temp files: {WORKDIR}")
    print(f"{'='*70}\n")
    
    return 0 if FAIL == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
