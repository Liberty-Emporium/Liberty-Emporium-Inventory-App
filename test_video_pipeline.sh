#!/bin/bash
# ============================================================
# 🧪 Video Pipeline Test Suite
# Run BEFORE deploying to Railway.
# Tests all Ken Burns directions + full pipeline with audio.
# Run from /root/liberty-app with: bash test_video_pipeline.sh
# ============================================================
set -e
WORKDIR=$(mktemp -d)
trap "rm -rf $WORKDIR" EXIT

PASS=0; FAIL=0
S="320x240"; F=10; D=10

ok()   { PASS=$((PASS+1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL+1)); echo "  ❌ $1 — $2"; }

echo ""
echo "========================================================"
echo "🎬 Video Pipeline Test — Liberty Emporium"
echo "========================================================"
echo "Dir: $WORKDIR"
echo ""

# ── 1. Individual zoompan expressions ──
echo "1/6: zoom_in"
ffmpeg -y -loglevel error -f lavfi -i "color=c=blue:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p $WORKDIR/t1.mp4 2>/dev/null \
  && [ -f $WORKDIR/t1.mp4 ] && ok "zoom_in" || fail "zoom_in"

echo "2/6: zoom_out"
ffmpeg -y -loglevel error -f lavfi -i "color=c=red:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p $WORKDIR/t2.mp4 2>/dev/null \
  && [ -f $WORKDIR/t2.mp4 ] && ok "zoom_out" || fail "zoom_out"

echo "3/6: pan_right"
ffmpeg -y -loglevel error -f lavfi -i "color=c=green:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='min(zoom+0.003,1.3)':x='if(eq(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p $WORKDIR/t3.mp4 2>/dev/null \
  && [ -f $WORKDIR/t3.mp4 ] && ok "pan_right" || fail "pan_right"

echo "4/6: pan_left"
ffmpeg -y -loglevel error -f lavfi -i "color=c=gold:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='min(zoom+0.003,1.3)':x='if(eq(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p $WORKDIR/t4.mp4 2>/dev/null \
  && [ -f $WORKDIR/t4.mp4 ] && ok "pan_left" || fail "pan_left"

# ── 5. Full pipeline: intro + 2 KB products + outro + xfade + audio ──
echo "5/6: Full pipeline (Ken Burns + audio)"
ffmpeg -y -loglevel error \
  -f lavfi -i "color=c=navy:s=640x480:d=1:r=10" \
  -f lavfi -i "color=c=blue:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=white@0:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=red:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=white@0:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=gold:s=640x480:d=1:r=10" \
  -f lavfi -i "sine=frequency=440:duration=10:sample_rate=44100" \
  -filter_complex "
  [0:v]fps=10[intro];
  [1:v]zoompan=z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=20:s=640x480:fps=10[kb0];
  [2:v]fade=in:st=0:d=0.8:alpha=1[tx0];
  [kb0][tx0]overlay=0:0[p0];
  [3:v]zoompan=z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=20:s=640x480:fps=10[kb1];
  [4:v]fade=in:st=0:d=0.8:alpha=1[tx1];
  [kb1][tx1]overlay=0:0[p1];
  [5:v]fps=10[outro];
  [intro][p0]xfade=transition=fade:duration=0.8:offset=0.2[c0];
  [c0][p1]xfade=transition=fade:duration=0.8:offset=2.200[c1];
  [c1][outro]xfade=transition=fade:duration=0.8:offset=4.200[outv];
  [outv]drawbox=x=0:y=474:w=iw*t/6.5:h=4:color=0xf0c040@0.95:t=fill[final]
  " \
  -map '[final]' -map '6:a' \
  -c:v libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p -c:a aac -b:a 128k \
  $WORKDIR/full.mp4 2>/dev/null

if [ -f $WORKDIR/full.mp4 ] && [ $(stat -c%s $WORKDIR/full.mp4) -gt 10000 ]; then
  HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv $WORKDIR/full.mp4 2>/dev/null | grep -c audio || true)
  if [ "$HAS_AUDIO" -gt 0 ]; then
    ok "Full pipeline ($(stat -c%s $WORKDIR/full.mp4) bytes, audio OK)"
  else
    fail "Full pipeline" "Missing audio stream"
  fi
else
  fail "Full pipeline" "No output or too small"
fi

# ── 6. Slideshow pipeline ──
echo "6/6: Slideshow pipeline (no Ken Burns)"
ffmpeg -y -loglevel error \
  -f lavfi -i "color=c=darkblue:s=640x480:d=1:r=10" \
  -f lavfi -i "color=c=teal:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=white@0:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=purple:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=white@0:s=640x480:d=2:r=10" \
  -f lavfi -i "color=c=orange:s=640x480:d=1:r=10" \
  -f lavfi -i "sine=frequency=880:duration=10:sample_rate=44100" \
  -filter_complex "
  [0:v]fps=10[intro];
  [1:v]fps=10[bg0];[3:v]fps=10[bg1];
  [2:v]fade=in:st=0:d=0.8:alpha=1[tx0];
  [4:v]fade=in:st=0:d=0.8:alpha=1[tx1];
  [bg0][tx0]overlay=0:0[p0];
  [bg1][tx1]overlay=0:0[p1];
  [5:v]fps=10[outro];
  [intro][p0]xfade=transition=fade:duration=0.8:offset=0.2[c0];
  [c0][p1]xfade=transition=fade:duration=0.8:offset=2.200[c1];
  [c1][outro]xfade=transition=fade:duration=0.8:offset=4.200[outv];
  [outv]drawbox=x=0:y=474:w=iw*t/6.5:h=4:color=0xf0c040@0.95:t=fill[final]
  " \
  -map '[final]' -map '6:a' \
  -c:v libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p -c:a aac -b:a 128k \
  $WORKDIR/slideshow.mp4 2>/dev/null

if [ -f $WORKDIR/slideshow.mp4 ] && [ $(stat -c%s $WORKDIR/slideshow.mp4) -gt 10000 ]; then
  HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv $WORKDIR/slideshow.mp4 2>/dev/null | grep -c audio || true)
  if [ "$HAS_AUDIO" -gt 0 ]; then
    ok "Slideshow ($(stat -c%s $WORKDIR/slideshow.mp4) bytes, audio OK)"
  else
    fail "Slideshow" "Missing audio stream"
  fi
else
  fail "Slideshow" "No output or too small"
fi

# ── Summary ──
echo ""
echo "========================================================"
echo "📊 $PASS passed, $FAIL failed"
if [ $FAIL -eq 0 ]; then
  echo "✅ ALL TESTS PASS — safe to deploy!"
else
  echo "❌ TESTS FAILED — do not deploy!"
fi
echo "========================================================"
echo ""
exit $FAIL
