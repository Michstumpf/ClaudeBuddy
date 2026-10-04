#include "lvgl_port.h"

#include <Arduino.h>
#include <lvgl.h>

#include "board.h"

namespace hal {

static constexpr int kBufLines = 24;
static uint16_t buf_a[board::kWidth * kBufLines], buf_b[board::kWidth * kBufLines];

static void flush(lv_display_t* disp, const lv_area_t* area, uint8_t* px_map) {
  board::push(area->x1, area->y1, area->x2 - area->x1 + 1, area->y2 - area->y1 + 1,
              reinterpret_cast<uint16_t*>(px_map));
  lv_display_flush_ready(disp);
}

static int tap_x = 0, tap_y = 0, tap_reads = 0;
static int drag_x1, drag_y1, drag_x2, drag_y2, drag_step = -1;
static constexpr int kDragSteps = 12;

void inject_tap(int x, int y) {
  tap_x = x;
  tap_y = y;
  tap_reads = 3;  // pressed for 2 reads, then released: a click
}

void inject_drag(int x1, int y1, int x2, int y2) {
  drag_x1 = x1, drag_y1 = y1, drag_x2 = x2, drag_y2 = y2;
  drag_step = 0;
}

static void read_touch(lv_indev_t*, lv_indev_data_t* data) {
  int x, y;
  if (drag_step >= 0) {
    const int k = drag_step > kDragSteps ? kDragSteps : drag_step;
    data->point.x = drag_x1 + (drag_x2 - drag_x1) * k / kDragSteps;
    data->point.y = drag_y1 + (drag_y2 - drag_y1) * k / kDragSteps;
    data->state = drag_step++ <= kDragSteps ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    if (data->state == LV_INDEV_STATE_RELEASED) drag_step = -1;
  } else if (tap_reads > 0) {
    data->point.x = tap_x;
    data->point.y = tap_y;
    data->state = --tap_reads > 0 ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
  } else if (board::touch(x, y)) {
    data->point.x = x;
    data->point.y = y;
    data->state = LV_INDEV_STATE_PRESSED;
  } else {
    data->state = LV_INDEV_STATE_RELEASED;
  }
}

static uint32_t tick() { return millis(); }

void lvgl_begin() {
  board::init();
  lv_init();
  lv_tick_set_cb(tick);
  lv_display_t* disp = lv_display_create(board::kWidth, board::kHeight);
  lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
  lv_display_set_flush_cb(disp, flush);
  lv_display_set_buffers(disp, buf_a, buf_b, sizeof(buf_a), LV_DISPLAY_RENDER_MODE_PARTIAL);

  lv_indev_t* touch = lv_indev_create();
  lv_indev_set_type(touch, LV_INDEV_TYPE_POINTER);
  lv_indev_set_read_cb(touch, read_touch);
}

void lvgl_loop() { lv_timer_handler(); }

}  // namespace hal
