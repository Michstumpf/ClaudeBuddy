// LVGL <-> panel glue: flush, touch input and tick. Board quirks live here.
#pragma once

namespace hal {
void lvgl_begin();  // panel + LVGL display + touch input device
void lvgl_loop();   // call from loop(): runs LVGL timers
// Test hook: press (x, y) for a few input reads, as if a finger tapped there
// (screen coordinates, before any board mirroring). Used by Wokwi scenarios.
void inject_tap(int x, int y);
// Test hook: press at (x1, y1), slide to (x2, y2) over a few reads, release.
void inject_drag(int x1, int y1, int x2, int y2);
}  // namespace hal
