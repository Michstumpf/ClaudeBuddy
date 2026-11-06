#!/bin/sh
# Regenerates src/ui/fonts: DejaVu Sans Mono with Latin-1 (ã, ç, é...) plus the
# UI's symbols from DejaVu Sans. LVGL's built-in fonts are ASCII only.
# Needs node (npx) and the DejaVu fonts (fonts-dejavu-core on Ubuntu).
set -e
cd "$(dirname "$0")/.."
DEJAVU=/usr/share/fonts/truetype/dejavu
TEXT=0x20-0x7E,0xA0-0xFF,0x2026,0x2016
# ✓ ✕ ◀ ▶ ⚙ ☁ ☀ ☾ ❄ ✦, the spinner: ✢ ✳ ✶ ✻ ✽, the pager ‹ ›, ■ (stop) and ♪ ♫ (focus music)
SYMBOLS=0x2713,0x2715,0x25C0,0x25B6,0x2699,0x2601,0x2600,0x263E,0x2744,0x2726,0x2722,0x2733,0x2736,0x273B,0x273D,0x2039,0x203A,0x25A0,0x266A,0x266B
for size in 11 13; do
  out=src/ui/fonts/buddy_font_$size.c
  npx -y lv_font_conv@1.5.2 --no-compress --bpp 4 --size $size --format lvgl \
    --font $DEJAVU/DejaVuSansMono.ttf -r $TEXT --font $DEJAVU/DejaVuSans.ttf -r $SYMBOLS \
    --lv-include lvgl.h -o $out
  # lv_font_conv targets LVGL 8: its LV_VERSION_CHECK(8, 0, 0) is false on LVGL 9
  # (the macro only matches the same major). Only the check guarding the public
  # descriptor must change, or the font loses const; the others guard an
  # LVGL 8-only glyph cache and must stay false.
  sed -i '/^#if LV_VERSION_CHECK(8, 0, 0)$/{N;s/^#if LV_VERSION_CHECK(8, 0, 0)\nconst lv_font_t/#if LVGL_VERSION_MAJOR >= 8\nconst lv_font_t/}' $out
done

# The pomodoro countdown: digits and ":" only, bold and big enough to read from the chair.
out=src/ui/fonts/buddy_font_48.c
npx -y lv_font_conv@1.5.2 --no-compress --bpp 4 --size 48 --format lvgl \
  --font $DEJAVU/DejaVuSansMono-Bold.ttf -r 0x30-0x3A \
  --lv-include lvgl.h -o $out
sed -i '/^#if LV_VERSION_CHECK(8, 0, 0)$/{N;s/^#if LV_VERSION_CHECK(8, 0, 0)\nconst lv_font_t/#if LVGL_VERSION_MAJOR >= 8\nconst lv_font_t/}' $out
