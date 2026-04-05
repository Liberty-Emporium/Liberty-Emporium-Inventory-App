#!/bin/bash
# ============================================================
# 🔧 Video Pipeline Test Suite - Liberty Emporium Ad Generator
# Run BEFORE deploying to Railway
# Usage: bash test_video.sh
# ============================================================
set -e
D=15; S=320x240; F=10
PASS=0; FAIL=0
TOTAL=0

ok()   { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo "  ❌ $1 — $2"; }

echo ""
echo "============================================================"
echo "🎬 VIDEO PIPELINE TEST — Liberty Emporium Ad Generator"
echo "============================================================"
echo ""

# ─── Phase 1: Individual zoompan expressions ───
echo "Phase 1: Ken Burns zoompan expressions"
echo "------------------------------------------------------------"

ffmpeg -y -loglevel error -f lavfi -i "color=c=blue:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='if(eq(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p /tmp/t0.mp4 2>/dev/null && ok "zoom_in" || fail "zoom_in" "ffmpeg failed"

ffmpeg -y -loglevel error -f lavfi -i "color=c=red:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='if(eq(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p /tmp/t1.mp4 2>/dev/null && ok "zoom_out" || fail "zoom_out" "ffmpeg failed"

ffmpeg -y -loglevel error -f lavfi -i "color=c=green:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='min(zoom+0.003,1.3)':x='if(eq(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p /tmp/t2.mp4 2>/dev/null && ok "pan_right" || fail "pan_right" "ffmpeg failed"

ffmpeg -y -loglevel error -f lavfi -i "color=c=gold:s=${S}:rate=${F}:d=0.1" \
  -vf "zoompan=z='min(zoom+0.003,1.3)':x='if(eq(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)':d=${D}:s=${S}:fps=${F}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p /tmp/t3.mp4 2>/dev/null && ok "pan_left" || fail "pan_left" "ffmpeg failed"

# ─── Phase 2: Full pipeline with 2 products ───
echo ""
echo "Phase 2: Full pipeline (intro→2 products→outro + xfade + audio)"
echo "------------------------------------------------------------"

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
  -c:v libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  /tmp/t_full.mp4 2>/dev/null

if [ -f /tmp/t_full.mp4 ] && [ $(stat -c%s /tmp/t_full.mp4) -gt 10000 ]; then
  HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv /tmp/t_full.mp4 2>/dev/null | grep -c audio || true)
  if [ "$HAS_AUDIO" -gt 0 ]; then
    ok "Full pipeline + audio ($(stat -c%s /tmp/t_full.mp4) bytes, audio present)"
  else
    fail "Full pipeline" "No audio stream in output"
  fi
else
  fail "Full pipeline" "No output file or too small"
fi

# ─── Summary ───
echo ""
echo "============================================================"
echo "📊 RESULTS: $PASS passed, $FAIL failed out of $TOTAL"
if [ $FAIL -eq 0 ]; then
  echo "✅ ALL TESTS PASS — Safe to deploy!"
else
  echo "❌ $FAIL TEST(S) FAILED — Do not deploy!"
fi
echo "============================================================"
echo ""

exit $FAIL
