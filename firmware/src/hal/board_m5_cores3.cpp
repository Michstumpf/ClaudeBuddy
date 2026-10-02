// M5Stack CoreS3: ESP32-S3, 2" 320x240 IPS (ILI9342C) with capacitive touch,
// AXP2101 power management, mics + speaker (used later for dictation and
// spoken replies). M5Unified knows the board's wiring.
#if defined(BOARD_M5_CORES3)
#include <M5Unified.h>

#include "board.h"

namespace hal::board {

void init() {
  auto cfg = M5.config();
  cfg.output_power = true;  // power the Grove port / bus from the battery too
  M5.begin(cfg);
  M5.Display.setRotation(1);  // landscape 320x240, USB port at the bottom
  M5.Display.setBrightness(160);
  M5.Display.fillScreen(TFT_BLACK);
}

void push(int x, int y, int w, int h, uint16_t* px) {
  M5.Display.pushImage(x, y, w, h, reinterpret_cast<lgfx::rgb565_t*>(px));
}

bool touch(int& x, int& y) {
  M5.update();
  if (M5.Touch.getCount() == 0) return false;
  const auto& t = M5.Touch.getDetail(0);
  if (!t.isPressed()) return false;
  x = t.x;
  y = t.y;
  return true;
}

int battery_percent() { return M5.Power.getBatteryLevel(); }
bool charging() { return M5.Power.isCharging() == m5::Power_Class::is_charging_t::is_charging; }

}  // namespace hal::board
#endif
