#include "lvgl_port.h"

#include <Arduino.h>
#include <lvgl.h>

#include <algorithm>

#include "display.h"

namespace hal {

static Display lcd;
static constexpr int kWidth = 320, kHeight = 240, kBufLines = 24;
static uint16_t buf_a[kWidth * kBufLines], buf_b[kWidth * kBufLines];

#if defined(BOARD_WOKWI_S3)
// Wokwi's ILI9341 model honors the row/column exchange bit of MADCTL but not
// the mirror bits, so every rotation shows up mirrored left-to-right. A real
// panel honors them; this is a simulator-only fix, applied to pixels and touch.
static constexpr bool kMirrorX = true;
#else
static constexpr bool kMirrorX = false;
#endif

static void flush(lv_display_t* disp, const lv_area_t* area, uint8_t* px_map) {
  const int w = area->x2 - area->x1 + 1, h = area->y2 - area->y1 + 1;
  auto* px = reinterpret_cast<uint16_t*>(px_map);
  int x = area->x1;
  if (kMirrorX) {
    for (int row = 0; row < h; row++) std::reverse(px + row * w, px + row * w + w);
    x = kWidth - 1 - area->x2;
  }
  lcd.pushImage(x, area->y1, w, h, reinterpret_cast<lgfx::rgb565_t*>(px));
  lv_display_flush_ready(disp);
}

static void read_touch(lv_indev_t*, lv_indev_data_t* data) {
  lgfx::touch_point_t tp;
  if (lcd.getTouch(&tp)) {
    data->state = LV_INDEV_STATE_PRESSED;
    data->point.x = kMirrorX ? kWidth - 1 - tp.x : tp.x;
    data->point.y = tp.y;
  } else {
    data->state = LV_INDEV_STATE_RELEASED;
  }
}

static uint32_t tick() { return millis(); }

void lvgl_begin() {
  lcd.init();
  lcd.setRotation(Display::kRotation);
  lcd.fillScreen(TFT_BLACK);

  lv_init();
  lv_tick_set_cb(tick);
  lv_display_t* disp = lv_display_create(kWidth, kHeight);
  lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
  lv_display_set_flush_cb(disp, flush);
  lv_display_set_buffers(disp, buf_a, buf_b, sizeof(buf_a), LV_DISPLAY_RENDER_MODE_PARTIAL);

  lv_indev_t* touch = lv_indev_create();
  lv_indev_set_type(touch, LV_INDEV_TYPE_POINTER);
  lv_indev_set_read_cb(touch, read_touch);
}

void lvgl_loop() { lv_timer_handler(); }

}  // namespace hal
