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
  // M5.begin leaves the microphone holding the shared I2S bus, so the speaker
  // never played. Speaker by default; recording takes the bus only while used.
  M5.Mic.end();
  M5.Speaker.begin();  // volume: the "volume" setting, applied by main
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
void set_brightness(uint8_t level) { M5.Display.setBrightness(level); }
void set_volume(uint8_t level) { M5.Speaker.setVolume(level); }

// ---- audio ------------------------------------------------------------------
// On the CoreS3 the mic and the speaker share the I2S bus: one at a time.

static constexpr uint32_t kRate = 16000;
static constexpr size_t kMaxSamples = kRate * 60;  // 60 s (the mic screen's limit), 1.9 MB in PSRAM
static constexpr size_t kChunk = kRate / 10;       // 100 ms per DMA request
static int16_t* rec_buf = nullptr;
static size_t rec_len = 0;
static bool recording = false;
static uint8_t* wav_copy = nullptr;  // playWav reads from it while playing

bool record_start() {
  if (!rec_buf) rec_buf = static_cast<int16_t*>(ps_malloc(kMaxSamples * sizeof(int16_t)));
  if (!rec_buf) return false;
  M5.Speaker.end();
  if (!M5.Mic.begin()) return false;
  rec_len = 0;
  recording = true;
  return true;
}

void record_loop() {
  if (!recording) return;
  // Keep two requests queued (M5.Mic double-buffers), until the buffer is full.
  while (M5.Mic.isRecording() < 2 && rec_len + kChunk <= kMaxSamples) {
    if (!M5.Mic.record(rec_buf + rec_len, kChunk, kRate)) break;
    rec_len += kChunk;
  }
}

int record_level() {
  // The newest chunk already filled: two more may still be queued for the DMA.
  if (!recording || rec_len < 3 * kChunk) return 0;
  const int16_t* chunk = rec_buf + rec_len - 3 * kChunk;
  double sum = 0;
  for (size_t i = 0; i < kChunk; i++) sum += static_cast<double>(chunk[i]) * chunk[i];
  const int rms = static_cast<int>(sqrt(sum / kChunk));
  return rms > 3000 ? 100 : rms / 30;  // speech at a desk: ~300-3000
}

float record_seconds() { return recording ? static_cast<float>(rec_len) / kRate : 0; }

size_t record_stop(const int16_t** samples) {
  recording = false;
  while (M5.Mic.isRecording()) delay(1);
  M5.Mic.end();
  M5.Speaker.begin();
  *samples = rec_buf;
  return rec_len;
}

bool play_wav(const uint8_t* wav, size_t len) {
  if (M5.Mic.isRunning()) {  // recording has the bus (isEnabled() only means "has a mic")
    Serial.println("audio: mic has the bus");
    return false;
  }
  M5.Speaker.stop();
  free(wav_copy);
  wav_copy = static_cast<uint8_t*>(ps_malloc(len));
  if (!wav_copy) {
    Serial.printf("audio: no memory for %u bytes (psram %u free)\n", (unsigned)len, (unsigned)ESP.getFreePsram());
    return false;
  }
  memcpy(wav_copy, wav, len);
  const bool ok = M5.Speaker.playWav(wav_copy, len);
  if (!ok) Serial.printf("audio: speaker refused (enabled=%d)\n", M5.Speaker.isEnabled());
  return ok;
}

bool playing() { return M5.Speaker.isPlaying(); }

// ---- sensors ----------------------------------------------------------------
// LTR-553ALS (proximity + ambient light) on the internal I2C bus; M5Unified
// has no driver for it, so the few registers needed are set here. IMU from
// M5Unified (BMI270). Thresholds are first guesses, to tune on the device.

static constexpr uint8_t kLtr = 0x23;
static constexpr uint32_t kI2cHz = 400000;
static bool ltr_ready = false;
static float last_mag = 1.0f;
static uint32_t down_since = 0;

static void ltr_begin() {
  ltr_ready = M5.In_I2C.writeRegister8(kLtr, 0x80, 0x01, kI2cHz)    // ALS_CONTR: active, gain 1x
              && M5.In_I2C.writeRegister8(kLtr, 0x81, 0x03, kI2cHz) // PS_CONTR: active
              && M5.In_I2C.writeRegister8(kLtr, 0x84, 0x02, kI2cHz); // PS_MEAS_RATE: 100 ms
}

Sensors read_sensors() {
  Sensors s;
  if (!ltr_ready) ltr_begin();
  if (ltr_ready) {
    uint8_t ps[2] = {0, 0}, als[4] = {0, 0, 0, 0};
    if (M5.In_I2C.readRegister(kLtr, 0x8D, ps, 2, kI2cHz)) {
      s.proximity = (ps[1] & 0x07) << 8 | ps[0];
      s.near = s.proximity > 400;
    }
    if (M5.In_I2C.readRegister(kLtr, 0x88, als, 4, kI2cHz)) s.light = als[2] | als[3] << 8;  // CH0 (visible + IR)
  }
  float ax, ay, az;
  if (M5.Imu.getAccel(&ax, &ay, &az)) {
    s.ax = ax, s.ay = ay, s.az = az;
    const float mag = sqrtf(ax * ax + ay * ay + az * az);
    s.shaken = fabsf(mag - last_mag) > 1.2f;  // g; a firm shake, not a tap on the screen
    last_mag = mag;
    // Screen facing the desk for a second. Measured on the CoreS3: az = -1 g
    // screen down, +1 g screen up, ay = +1 g standing.
    if (az < -0.85f) {
      if (!down_since) down_since = millis();
    } else {
      down_since = 0;
    }
    s.face_down = down_since && millis() - down_since > 1000;
    s.face_up = az > 0.85f;
  }
  return s;
}

}  // namespace hal::board
#endif
