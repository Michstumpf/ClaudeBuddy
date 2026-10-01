// LVGL <-> panel glue: flush, touch input and tick. Board quirks live here.
#pragma once

namespace hal {
void lvgl_begin();  // panel + LVGL display + touch input device
void lvgl_loop();   // call from loop(): runs LVGL timers
// Test hook: press (x, y) for a few input reads, as if a finger tapped there
// (screen coordinates, before any board mirroring). Used by Wokwi scenarios.
void inject_tap(int x, int y);
}  // namespace hal
