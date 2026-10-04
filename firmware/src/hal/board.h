// What a board must provide. One implementation per board (board_*.cpp), picked
// by the build flags in platformio.ini; nothing outside src/hal knows which.
#pragma once
#include <stdint.h>

namespace hal::board {

// Capabilities the UI adapts to (e.g. no dictation button without a mic).
#if defined(BOARD_M5_CORES3)
constexpr bool kHasAudio = true, kHasBattery = true;
#else
constexpr bool kHasAudio = false, kHasBattery = false;
#endif

constexpr int kWidth = 320, kHeight = 240;

void init();
// RGB565 pixels for the area (x, y, w, h), in screen coordinates.
void push(int x, int y, int w, int h, uint16_t* pixels);
// Current touch point in screen coordinates, if the screen is touched.
bool touch(int& x, int& y);
// Battery charge 0-100, or -1 without a battery.
int battery_percent();
bool charging();
// Backlight 0-255 (no-op without a dimmable backlight).
void set_brightness(uint8_t level);

}  // namespace hal::board
