// Wokwi simulation: ESP32-S3 + ILI9341 320x240 (SPI) + FT6206 touch (I2C),
// wired as in diagram.json.
#if defined(BOARD_WOKWI_S3)
#include <algorithm>

#include "board.h"
#include "display.h"

namespace hal::board {

static Display lcd;

// Wokwi's ILI9341 model honors MADCTL's row/column exchange but not its mirror
// bits, so every rotation shows up mirrored left-to-right. A real panel honors
// them; this simulator-only fix mirrors pixels and touch.
static constexpr bool kMirrorX = true;

void init() {
  lcd.init();
  lcd.setRotation(Display::kRotation);
  lcd.fillScreen(TFT_BLACK);
}

void push(int x, int y, int w, int h, uint16_t* px) {
  if (kMirrorX) {
    for (int row = 0; row < h; row++) std::reverse(px + row * w, px + row * w + w);
    x = kWidth - x - w;
  }
  lcd.pushImage(x, y, w, h, reinterpret_cast<lgfx::rgb565_t*>(px));
}

bool touch(int& x, int& y) {
  lgfx::touch_point_t tp;
  if (!lcd.getTouch(&tp)) return false;
  x = kMirrorX ? kWidth - 1 - tp.x : tp.x;
  y = tp.y;
  return true;
}

int battery_percent() { return -1; }
bool charging() { return false; }

}  // namespace hal::board
#endif
