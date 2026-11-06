// Claude Buddy firmware entry point: wires the link (serial for now; the
// WebSocket to the hub comes next), the protocol and the UI.
#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>

#include <lvgl.h>

#include "app/protocol.h"
#include "hal/board.h"
#include "hal/lvgl_port.h"
#include <vector>

#include "net/audio_http.h"
#include "net/hub_link.h"
#include "net/serial_link.h"
#include "ui/ui.h"

// Commands that only come over USB serial, never from the hub:
//   {"type":"_tap","x":..,"y":..}  test tap (Wokwi scenarios)
//   {"type":"_battery","percent":..,"charging":..}  fake battery level (Wokwi has none)
//   {"type":"_config","ssid":..,"password":..,"host":..,"port":..,"token":..}
//                                  save WiFi + hub to flash and restart
//   {"type":"_sensors"}            print raw sensor readings every 200 ms (again: stop)
//   {"type":"_shake"}              act as if the Buddy was shaken (asks the hub for a joke)
//   {"type":"_stats"}              print Ajustes → Sistema's numbers every second (again: stop)
static bool stats_log_ = false;
static bool sensor_log_ = false;
static void send_to_hub(const std::string& json);

static bool local_command(const std::string& json) {
  if (json.find("\"_tap\"") == std::string::npos && json.find("\"_config\"") == std::string::npos &&
      json.find("\"_battery\"") == std::string::npos && json.find("\"_drag\"") == std::string::npos &&
      json.find("\"_sensors\"") == std::string::npos && json.find("\"_shake\"") == std::string::npos &&
      json.find("\"_stats\"") == std::string::npos)
    return false;
  JsonDocument doc;
  if (deserializeJson(doc, json)) return false;
  if (doc["type"] == "_tap") {
    hal::inject_tap(doc["x"] | 0, doc["y"] | 0);
    Serial.printf("rx: tap %d,%d\n", (int)(doc["x"] | 0), (int)(doc["y"] | 0));
    return true;
  }
  if (doc["type"] == "_drag") {
    hal::inject_drag(doc["x1"] | 0, doc["y1"] | 0, doc["x2"] | 0, doc["y2"] | 0);
    Serial.println("rx: drag");
    return true;
  }
  if (doc["type"] == "_battery") {
    ui::set_battery(doc["percent"] | -1, doc["charging"] | false);
    Serial.printf("rx: battery %d\n", (int)(doc["percent"] | -1));
    return true;
  }
  if (doc["type"] == "_stats") {
    stats_log_ = !stats_log_;
    return true;
  }
  if (doc["type"] == "_shake") {
    Serial.println("rx: shake");
    ui::on_shake();
    send_to_hub(app::joke_now());
    return true;
  }
  if (doc["type"] == "_sensors") {
    sensor_log_ = !sensor_log_;
    Serial.printf("rx: sensor log %s\n", sensor_log_ ? "on" : "off");
    return true;
  }
  if (doc["type"] == "_config") {
    net::HubConfig c = net::load_config();
    if (doc["ssid"].is<const char*>()) c.ssid = doc["ssid"].as<const char*>();
    if (doc["password"].is<const char*>()) c.password = doc["password"].as<const char*>();
    if (doc["host"].is<const char*>()) c.host = doc["host"].as<const char*>();
    if (doc["token"].is<const char*>()) c.token = doc["token"].as<const char*>();
    c.port = doc["port"] | c.port;
    net::save_config(c);
    Serial.println("config saved, restarting");
    delay(200);
    ESP.restart();
  }
  return false;
}

// Everything the Buddy says goes to the hub, and is echoed on serial (tests).
static void send_to_hub(const std::string& json) {
  net::serial_send(json);
  net::hub_send(json);
}

static void on_status(const char* status, bool connected) {
  Serial.printf("link: %s\n", status);
  ui::set_link(status, connected);
}

static bool night_ = false;
static net::HubConfig cfg_;
static bool near_ = false;
static int light_ = -1;

// Backlight: dim at night and on low battery (CoreS3; no-op in Wokwi).
static void update_brightness(int battery, bool charging) {
  // Ambient light sets the base (dim room, dim screen); someone close wakes it.
  // The "brightness" setting: auto follows the ambient light; a fixed level ignores it.
  const std::string& setting = ui::settings().brightness;
  uint8_t level;
  if (setting == "low") level = 60;
  else if (setting == "mid") level = 130;
  else if (setting == "high") level = 220;
  else {
    level = light_ < 0 ? 160 : light_ < 20 ? 60 : light_ < 200 ? 120 : 200;
    if (near_) level = level < 160 ? 160 : level;
  }
  if (battery >= 0 && battery <= 15 && !charging) level = 70;
  if (night_) level = 20;
  hal::board::set_brightness(level);
}

// Spoken replies, jokes and notices come as a URL on the hub: fetch and play.
static void play(const std::string& url) {
  if (!hal::board::kHasAudio || url.empty() || night_ || ui::settings().volume == "off") return;
  std::vector<uint8_t> wav;
  const uint32_t started = millis();
  const bool got = net::download(cfg_, url, wav);
  const bool playing = got && hal::board::play_wav(wav.data(), wav.size());
  Serial.printf("audio: download %s (%u bytes, %lu ms), play %s\n", got ? "ok" : "failed", (unsigned)wav.size(),
                (unsigned long)(millis() - started), playing ? "started" : "not started");
}

// ---- push-to-talk on the device (CoreS3 mic) ----
static void start_dictation() {
  if (!hal::board::record_start()) {
    ui::on_notice("Não consegui ligar o microfone.");
    return;
  }
  ui::set_listening(true);
}

static void stop_dictation(const std::string& session_id) {
  const int16_t* pcm = nullptr;
  const size_t samples = hal::board::record_stop(&pcm);
  ui::set_listening(false);
  if (samples < 16000 * 4 / 10) {  // < 0.4 s: a tap, not speech
    ui::on_notice("Muito curto: segure enquanto fala.");
    return;
  }
  ui::show_message("Transcrevendo…", "");
  net::Transcription t = net::transcribe(cfg_, pcm, samples);
  ui::show_message("", "");  // clears the overlay (see show_message)
  if (!t.ok) {
    ui::on_notice(("Não consegui transcrever. " + t.detail).c_str());
  } else if (t.handled) {
    ui::on_notice(t.detail);  // "enviado para HIPAA", "status de DataHub"…
  } else if (t.text.empty()) {
    ui::on_notice("Não ouvi nada.");
  } else if (!session_id.empty()) {
    send_to_hub(app::dictate(session_id, t.text));
    ui::on_notice("Enviado: " + t.text);
  } else {
    ui::on_notice("Para ditar numa sessão, abra-a e segure Ditar. Ou diga: manda para a <sessão>: …");
  }
}

// The 🎤 screen: record until Enviar/Cancelar, then only transcribe (the user
// reads the text and picks a session, the focused window or a voice command).
static bool mic_start() { return hal::board::record_start(); }
static int mic_level() { return hal::board::record_level(); }
static float mic_seconds() { return hal::board::record_seconds(); }
static void mic_finish(bool send) {
  const int16_t* pcm = nullptr;
  const size_t samples = hal::board::record_stop(&pcm);
  if (!send) return;
  if (samples < 16000 * 4 / 10) {  // < 0.4 s: nothing said
    ui::show_transcript(true, "");
    return;
  }
  net::Transcription t = net::transcribe(cfg_, pcm, samples, 16000, false);
  Serial.printf("mic: %.1fs recorded, transcription %s (%u chars)\n", samples / 16000.0f, t.ok ? "ok" : "failed",
                (unsigned)t.text.size());
  ui::show_transcript(t.ok, t.ok ? t.text : t.detail);
}

static void on_message(const std::string& json) {
  if (local_command(json)) return;
  app::Incoming in = app::parse(json);
  switch (in.type) {
    case app::MessageType::State:
      ui::apply(in.state, true);
      night_ = in.state.night;
      // Settings may have changed (here or in the web simulator): volume and brightness.
      {
        const std::string& v = in.state.settings.volume;
        hal::board::set_volume(v == "off" ? 0 : v == "low" ? 90 : v == "high" ? 255 : 170);
      }
      update_brightness(hal::board::battery_percent(), hal::board::charging());
      Serial.printf("rx: state sessions=%u pending=%u night=%d\n", (unsigned)in.state.sessions.size(),
                    (unsigned)in.state.pending.size(), in.state.night);
      break;
    case app::MessageType::Joke:
      ui::on_joke(in.text);
      Serial.println("rx: joke");
      play(in.url);
      break;
    case app::MessageType::Speech:
      Serial.println("rx: speech");
      play(in.url);
      break;
    case app::MessageType::DictateResult:
    case app::MessageType::CommandResult:
      ui::on_send_result(in.ok, in.text);
      break;
    case app::MessageType::Notice:
      ui::on_notice(in.text);
      Serial.println("rx: notice");
      play(in.url);
      break;
    default:
      break;
  }
}

static void open_setup_portal() { net::run_setup_portal(ui::show_message); }

void setup() {
  // Hub state messages run to a few KB; the default 256-byte RX buffer overflows
  // while LVGL is rendering and the JSON arrives truncated.
  Serial.setRxBufferSize(8192);
  Serial.begin(115200);
  hal::lvgl_begin();
  ui::begin(send_to_hub);
  net::serial_begin(on_message);
  ui::set_setup_handler(open_setup_portal);
  if (hal::board::kHasAudio) {
    ui::set_dictation_handlers(start_dictation, stop_dictation);
    ui::MicHandlers mic;
    mic.start = mic_start;
    mic.level = mic_level;
    mic.seconds = mic_seconds;
    mic.finish = mic_finish;
    ui::set_mic_handlers(mic);
  }
#if !defined(BOARD_WOKWI_S3)  // Wokwi can't host an access point: keep the serial link there
  if (!net::configured()) open_setup_portal();  // first boot: set up from the phone
#endif
  cfg_ = net::load_config();
  net::hub_begin(cfg_, on_message, on_status);
  Serial.println("buddy: ready");
}

// Ajustes → Sistema: how busy the loop is (time working vs. waiting in delay),
// memory, flash and WiFi, sent to the screen once a second.
static void report_stats(uint32_t busy_us) {
  static uint32_t firmware = 0, firmware_max = 0;
  if (!firmware) {  // reading the image size is slow: once
    firmware = ESP.getSketchSize();
    firmware_max = firmware + ESP.getFreeSketchSpace();
  }
  ui::Stats st;
  st.cpu = busy_us / 10000;  // of 1 s
  st.heap_free = ESP.getFreeHeap();
  st.heap_total = ESP.getHeapSize();
  st.psram_free = ESP.getFreePsram();
  st.psram_total = ESP.getPsramSize();
  st.firmware = firmware;
  st.firmware_max = firmware_max;
  if (WiFi.status() == WL_CONNECTED) {
    st.rssi = WiFi.RSSI();
    st.ip = WiFi.localIP().toString().c_str();
  }
  st.uptime_s = millis() / 1000;
  ui::set_stats(st);
  if (stats_log_)
    Serial.printf("stats: cpu=%d%% heap=%lu/%luKB psram=%lu/%luKB rssi=%d\n", st.cpu, (unsigned long)(st.heap_free / 1024),
                  (unsigned long)(st.heap_total / 1024), (unsigned long)(st.psram_free / 1024),
                  (unsigned long)(st.psram_total / 1024), st.rssi);
}

void loop() {
  const uint32_t loop_started = micros();
  static uint32_t busy_us = 0, window_at = 0;
  net::serial_loop();
  net::hub_loop();
  hal::board::record_loop();
  if (hal::board::kHasSensors) {
    static uint32_t sensors_at = 0, joke_at = 0;
    static bool was_down = false;
    if (millis() - sensors_at > 200) {
      sensors_at = millis();
      const hal::board::Sensors s = hal::board::read_sensors();
      if (sensor_log_)
        Serial.printf("sensors: ax=%.2f ay=%.2f az=%.2f ps=%d light=%d down=%d\n", s.ax, s.ay, s.az, s.proximity,
                      s.light, s.face_down);
      // Shake: ask for a joke (at most every 30 s).
      if (s.shaken && millis() - joke_at > 30000) {
        joke_at = millis();
        ui::on_shake();
        send_to_hub(app::joke_now());
      }
      // Screen down on the desk: night mode; picked up: awake.
      const app::Settings& prefs = ui::settings();
      if (s.face_down != was_down) {
        was_down = s.face_down;
        if (prefs.face_down_sleep) send_to_hub(app::night(s.face_down));
      }
      // Lying flat, screen up (no scratches): night mode after a minute without a
      // touch, so it never dozes off while in use; picked up: awake. A touch wakes
      // it through the hub, and it needs another untouched minute to doze again.
      static bool flat_asleep = false;
      if (s.face_up && !flat_asleep && prefs.flat_sleep_min > 0 &&
          lv_display_get_inactive_time(nullptr) > prefs.flat_sleep_min * 60000u) {
        flat_asleep = true;
        send_to_hub(app::night(true));
      } else if (flat_asleep && (!s.face_up || lv_display_get_inactive_time(nullptr) < 1000)) {
        flat_asleep = false;
        if (!s.face_up) send_to_hub(app::night(false));
      }
      if (s.near != near_ || (s.light >= 0 && abs(s.light - light_) > 30)) {
        near_ = s.near;
        if (s.light >= 0) light_ = s.light;
        if (near_) lv_display_trigger_activity(nullptr);
        update_brightness(hal::board::battery_percent(), hal::board::charging());
      }
    }
  }
  // Battery: every 30 s, or right away when the charger is plugged/unplugged.
  static uint32_t battery_at = 0;
  static bool was_charging = false;
  if (hal::board::kHasBattery) {
    const bool charging = hal::board::charging();
    if (battery_at == 0 || millis() - battery_at > 30000 || charging != was_charging) {
      battery_at = millis();
      was_charging = charging;
      const int percent = hal::board::battery_percent();
      ui::set_battery(percent, charging);
      send_to_hub(app::battery(percent, charging));
      update_brightness(percent, charging);
    }
  }
  ui::loop();
  hal::lvgl_loop();
  busy_us += micros() - loop_started;
  if (millis() - window_at >= 1000) {
    report_stats(busy_us);
    busy_us = 0;
    window_at = millis();
  }
  delay(5);
}
