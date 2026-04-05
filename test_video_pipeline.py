#!/usr/bin/env python3
"""
test_video_pipeline.py - Test the video generation pipeline locally.
Run BEFORE pushing to Railway.
Usage: python3 test_video_pipeline.py
Tests all 4 Ken Burns directions + audio + full pipeline.
"""
import subprocess, os, sys, tempfile, json

PASS = FAIL = 0

def run(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stderr[-500:] if r.stderr else ""

def check(name, ok, msg=""):
    global PASS, FAIL
    ok and (PASS := PASS + 1) or (FAIL := FAIL + 1)
    PASS = PASS if ok else PASS
    FAIL = FAIL if not ok else FAIL
    status = "✅" if ok else "❌"
    if ok: PASS_val = 1
    else: FAIL_val = 1
    pass_count = PASS + (1 if ok else 0)
    fail_count = FAIL + (0 if ok else 0)
    print(f"  {status} {name} {msg}")

# Need to track properly
_pass = _fail = 0

def result(name, ok, msg=""):
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  ✅ {name} {msg}")
    else:
        _fail += 1
        print(f"  ❌ {name} FAIL: {msg}")

def main():
    global _pass, _fail
    tmpdir = tempfile.mkdtemp(prefix='vtest_')
    fps = 25
    
    print("\n" + "="*60)
    print("🎬 VIDEO PIPELINE TEST — Liberty Emporium Ad Generator")
    print("="*60 + "\n")
    
    # ── Step 1: Test each zoompan direction individually (fast) ──
    print("Step 1: Ken Burns zoompan expressions (individual)")
    directions = [
        ("zoom_in",  "z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("zoom_out", "z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"),
        ("pan_rtol", "z='min(zoom+0.003,1.3)':x='if(eq(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)'"),
        ("pan_ltor", "z='min(zoom+0.003,1.3)':x='if(eq(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)'"),
    ]
    for name, expr in directions:
        cmd = [
            'ffmpeg', '-y', '-nostats', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:rate=25:d=0.5',
            '-vf', f"zoompan={expr}:d=12:s=320x240:fps=25",
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            f'{tmpdir}/{name}.mp4'
        ]
        rc, err = run(cmd, timeout=60)
        ok_file = os.path.exists(f'{tmpdir}/{name}.mp4') and os.path.getsize(f'{tmpdir}/{name}.mp4') > 0
        result(f"  {name}", rc == 0 and ok_file, err.strip() if rc != 0 else f"OK")
    
    # ── Step 2: Test full pipeline (intro -> 2 products -> outro + xfade + audio) ──
    print("\nStep 2: Full pipeline with 2 products + audio + xfade")
    
    # Generate test images as JPEG
    for i, c in enumerate(['navy', 'red']):
        run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 
             f'color=c={c}:s=640x480:d=0.04:r=1', '-frames:v', '1', '-update', '1',
             f'{tmpdir}/p{i}.jpg'], timeout=15)
    
    # Generate test audio
    run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=20:sample_rate=44100', '-b:a', '128k',
         f'{tmpdir}/audio.mp3'], timeout=15)
    
    # Generate transparent text overlay images
    for i in range(2):
        run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
             f'color=c=black@0.0:s=640x480:d=0.04:r=1', '-frames:v', '1', '-update', '1',
             f'{tmpdir}/tx{i}.png'], timeout=15)
    
    W, H = 640, 480
    t_per = 3.0
    intro_dur = 2.5
    outro_dur = 3.0
    crossfade = 0.8
    total_dur = intro_dur + 2*t_per + outro_dur
    
    # Full filter complex matching the actual app code
    filter_complex = (
        f"[0:v]fps=25[intro];"
        f"[1:v]zoompan=z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=75:s={W}x{H}:fps=25[bg0];"
        f"[3:v]zoompan=z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=75:s={W}x{H}:fps=25[bg1];"
        f"[2:v]fade=in:st=0:d=0.8:alpha=1[tx0];"
        f"[4:v]fade=in:st=0:d=0.8:alpha=1[tx1];"
        f"[bg0][tx0]overlay=0:0[p0];"
        f"[bg1][tx1]overlay=0:0[p1];"
        f"[5:v]fps=25[outro];"
        f"[intro][p0]xfade=transition=fade:duration={crossfade}:offset={intro_dur-crossfade}[c0];"
        f"[c0][p1]xfade=transition=fade:duration={crossfade}:offset={intro_dur+t_per-crossfade:.1f}[c1];"
        f"[c1][outro]xfade=transition=fade:duration={crossfade}:offset={intro_dur+2*t_per-crossfade:.1f}[outv];"
        f"[outv]drawbox=x=0:y={H-6}:w=iw*t/{total_dur:.1f}:h=4:color=0xf0c040@0.95:t=fill[final]"
    )
    
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'warning',
        # 0: intro card
        '-loop', '1', '-framerate', '25', '-t', str(intro_dur), '-i', f'{tmpdir}/p0.jpg',
        # 1: product 0 bg
        '-loop', '1', '-framerate', '25', '-t', str(t_per), '-i', f'{tmpdir}/p0.jpg',
        # 2: product 0 text overlay
        '-loop', '1', '-framerate', '25', '-t', str(t_per), '-i', f'{tmpdir}/tx0.png',
        # 3: product 1 bg
        '-loop', '1', '-framerate', '25', '-t', str(t_per), '-i', f'{tmpdir}/p1.jpg',
        # 4: product 1 text overlay
        '-loop', '1', '-framerate', '25', '-t', str(t_per), '-i', f'{tmpdir}/tx1.png',
        # 5: outro card
        '-f', 'lavfi', '-i', f'color=c=gold:s={W}x{H}:d={outro_dur}:r=25',
        # 6: audio
        '-i', f'{tmpdir}/audio.mp3',
        '-filter_complex', filter_complex,
        '-map', '[final]',
        '-map', '6:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k', '-pix_fmt', 'yuv420p',
        '-shortest', '-t', str(total_dur),
        f'{tmpdir}/full_video.mp4'
    ]
    
    rc, err = run(cmd, timeout=180)
    out_path = f'{tmpdir}/full_video.mp4'
    has_file = os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    
    if has_file:
        # Check for audio stream
        ar, aout = run(['ffprobe', '-v', 'error', '-select_streams', 'a',
                        '-show_entries', 'stream=codec_type', '-of', 'csv', out_path], timeout=10)
        has_audio = 'audio' in aout
        size_kb = os.path.getsize(out_path) // 1024
        result("Full pipeline", rc == 0 and has_file and has_audio,
              f"{size_kb}KB, {'✓ audio' if has_audio else '✗ NO AUDIO'}" + (f" | {err[:100]}" if rc != 0 else ""))
    else:
        result("Full pipeline", False, err[:200])
    
    # ── Step 3: Test Slideshow variant (no zoompan) ──
    print("\nStep 3: Slideshow pipeline (no Ken Burns)")
    filter_complex_ss = (
        f"[0:v]fps=25[intro];"
        f"[1:v]fps=25[bg0];[3:v]fps=25[bg1];"
        f"[2:v]fade=in:st=0:d=0.8:alpha=1[tx0];[4:v]fade=in:st=0:d=0.8:alpha=1[tx1];"
        f"[bg0][tx0]overlay=0:0[p0];[bg1][tx1]overlay=0:0[p1];"
        f"[5:v]fps=25[outro];"
        f"[intro][p0]xfade=transition=fade:duration={crossfade}:offset={intro_dur-crossfade}[c0];"
        f"[c0][p1]xfade=transition=fade:duration={crossfade}:offset={intro_dur+t_per-crossfade:.1f}[c1];"
        f"[c1][outro]xfade=transition=fade:duration={crossfade}:offset={intro_dur+2*t_per-crossfade:.1f}[outv];"
        f"[outv]drawbox=x=0:y={H-6}:w=iw*t/{total_dur:.1f}:h=4:color=0xf0c040@0.95:t=fill[final]"
    )
    
    cmd_ss = cmd[:1] + cmd[2:-3] + [
        '-filter_complex', filter_complex_ss,
        '-map', '[final]', '-map', '6:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k', '-pix_fmt', 'yuv420p',
        '-shortest', '-t', str(total_dur), f'{tmpdir}/full_ss.mp4'
    ]
    rc2, err2 = run(cmd_ss, timeout=120)
    has_file2 = os.path.exists(f'{tmpdir}/full_ss.mp4') and os.path.getsize(f'{tmpdir}/full_ss.mp4') > 1000
    result("Slideshow + audio", rc2 == 0 and has_file2,
           f"{os.path.getsize(f'{tmpdir}/full_ss.mp4')//1024}KB" if has_file2 else err2[:200])
    
    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"📊 RESULTS: {_pass} passed, {_fail} failed")
    if _fail == 0:
        print("🎉 ALL TESTS PASSED — safe to deploy!")
    else:
        print("⚠️  SOME TESTS FAILED — DO NOT DEPLOY")
    print(f"📁 Temp files: {tmpdir}")
    print(f"{'='*60}\n")
    
    return 0 if _fail == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
