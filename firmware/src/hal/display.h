// LovyanGFX panel + touch setup for the boards that are wired by hand (Wokwi).
// Boards with a vendor library (M5Stack CoreS3 via M5Unified) don't use this.
#pragma once

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#if defined(BOARD_WOKWI_S3)
// Wokwi: ILI9341 320x240 on SPI + FT6206 capacitive touch on I2C (diagram.json).
class Display : public lgfx::LGFX_Device {
 public:
  static constexpr uint8_t kRotation = 1;  // landscape; diagram.json mounts the panel rotated 270

 private:
  lgfx::Panel_ILI9341 panel_;
  lgfx::Bus_SPI bus_;
  lgfx::Touch_FT5x06 touch_;

 public:
  Display() {
    auto bus = bus_.config();
    bus.spi_host = SPI2_HOST;
    bus.spi_mode = 0;
    bus.freq_write = 40000000;
    bus.freq_read = 16000000;
    bus.pin_sclk = 12;
    bus.pin_mosi = 11;
    bus.pin_miso = 13;
    bus.pin_dc = 9;
    bus_.config(bus);
    panel_.setBus(&bus_);

    auto panel = panel_.config();
    panel.pin_cs = 10;
    panel.pin_rst = 14;
    panel.panel_width = 240;
    panel.panel_height = 320;
    panel_.config(panel);

    auto touch = touch_.config();
    touch.i2c_port = 0;
    touch.pin_sda = 6;
    touch.pin_scl = 7;
    touch.i2c_addr = 0x38;
    touch.freq = 400000;
    touch.x_min = 0;
    touch.x_max = 239;
    touch.y_min = 0;
    touch.y_max = 319;
    touch_.config(touch);
    panel_.setTouch(&touch_);

    setPanel(&panel_);
  }
};
#endif
