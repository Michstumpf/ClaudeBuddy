// LVGL <-> panel glue: flush, touch input and tick. Board quirks live here.
#pragma once

namespace hal {
void lvgl_begin();  // panel + LVGL display + touch input device
void lvgl_loop();   // call from loop(): runs LVGL timers
}  // namespace hal
