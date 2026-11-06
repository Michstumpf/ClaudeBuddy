// What a board must provide. One implementation per board (board_*.cpp), picked
// by the build flags in platformio.ini; nothing outside src/hal knows which.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace hal::board {

// Capabilities the UI adapts to (e.g. no dictation button without a mic).
#if defined(BOARD_M5_CORES3)
constexpr bool kHasAudio = true, kHasBattery = true, kHasSensors = true;
#else
constexpr bool kHasAudio = false, kHasBattery = false, kHasSensors = false;
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
void set_volume(uint8_t level);  // speaker, 0 = mute .. 255
bool power_clicked();  // a short press of the power button since the last call
void power_off();      // cuts the power (with USB plugged in, the charger may wake it again)

// ---- audio (kHasAudio). Mic and speaker may share one bus: recording stops playback.
// 16 kHz mono PCM. record_loop() must be called often while recording.
bool record_start();
void record_loop();
size_t record_stop(const int16_t** samples);  // returns the sample count; valid until the next record_start
int record_level();      // loudness of the latest 100 ms, 0..100 (the recording screen's wave)
float record_seconds();  // recorded so far
bool play_wav(const uint8_t* wav, size_t len);  // copies the data; plays in the background
bool playing();

// ---- sensors (kHasSensors). Values the UI and main can act on.
struct Sensors {
  bool near = false;      // something close to the screen (proximity)
  int light = -1;         // ambient light, 0-1000ish, -1 unknown
  bool face_down = false; // screen facing the desk
  bool face_up = false;   // lying flat, screen up (main: asleep after a minute untouched)
  bool shaken = false;    // a shake since the last read
  // Raw readings, for tuning the thresholds ({"type":"_sensors"} over USB).
  float ax = 0, ay = 0, az = 0;  // acceleration in g
  int proximity = -1;            // LTR-553 PS count, 0-2047
};
Sensors read_sensors();

}  // namespace hal::board
