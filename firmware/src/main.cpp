// Claude Buddy firmware entry point: wires the link (serial for now; the
// WebSocket to the hub comes next), the protocol and the UI.
#include <Arduino.h>
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
static bool local_command(const std::string& json) {
  if (json.find("\"_tap\"") == std::string::npos && json.find("\"_config\"") == std::string::npos &&
      json.find("\"_battery\"") == std::string::npos && json.find("\"_drag\"") == std::string::npos)
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
  uint8_t level = light_ < 0 ? 160 : light_ < 20 ? 60 : light_ < 200 ? 120 : 200;
  if (near_) level = level < 160 ? 160 : level;
  if (battery >= 0 && battery <= 15 && !charging) level = 70;
  if (night_) level = 20;
  hal::board::set_brightness(level);
}

// Spoken replies, jokes and notices come as a URL on the hub: fetch and play.
static void play(const std::string& url) {
  if (!hal::board::kHasAudio || url.empty() || night_) return;
  std::vector<uint8_t> wav;
  if (net::download(cfg_, url, wav)) hal::board::play_wav(wav.data(), wav.size());
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

static void on_message(const std::string& json) {
  if (local_command(json)) return;
  app::Incoming in = app::parse(json);
  switch (in.type) {
    case app::MessageType::State:
      ui::apply(in.state, true);
      if (in.state.night != night_) {
        night_ = in.state.night;
        update_brightness(hal::board::battery_percent(), hal::board::charging());
      }
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
  if (hal::board::kHasAudio) ui::set_dictation_handlers(start_dictation, stop_dictation);
#if !defined(BOARD_WOKWI_S3)  // Wokwi can't host an access point: keep the serial link there
  if (!net::configured()) open_setup_portal();  // first boot: set up from the phone
#endif
  cfg_ = net::load_config();
  net::hub_begin(cfg_, on_message, on_status);
  Serial.println("buddy: ready");
}

void loop() {
  net::serial_loop();
  net::hub_loop();
  hal::board::record_loop();
  if (hal::board::kHasSensors) {
    static uint32_t sensors_at = 0, joke_at = 0;
    static bool was_down = false;
    if (millis() - sensors_at > 200) {
      sensors_at = millis();
      const hal::board::Sensors s = hal::board::read_sensors();
      // Shake: ask for a joke (at most every 30 s).
      if (s.shaken && millis() - joke_at > 30000) {
        joke_at = millis();
        send_to_hub(app::joke_now());
      }
      // Screen down on the desk: night mode; picked up: awake.
      if (s.face_down != was_down) {
        was_down = s.face_down;
        send_to_hub(app::night(s.face_down));
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
  delay(5);
}
