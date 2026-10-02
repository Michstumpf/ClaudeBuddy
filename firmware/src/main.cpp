// Claude Buddy firmware entry point: wires the link (serial for now; the
// WebSocket to the hub comes next), the protocol and the UI.
#include <Arduino.h>
#include <ArduinoJson.h>

#include "app/protocol.h"
#include "hal/board.h"
#include "hal/lvgl_port.h"
#include "net/hub_link.h"
#include "net/serial_link.h"
#include "ui/ui.h"

// Commands that only come over USB serial, never from the hub:
//   {"type":"_tap","x":..,"y":..}  test tap (Wokwi scenarios)
//   {"type":"_config","ssid":..,"password":..,"host":..,"port":..,"token":..}
//                                  save WiFi + hub to flash and restart
static bool local_command(const std::string& json) {
  if (json.find("\"_tap\"") == std::string::npos && json.find("\"_config\"") == std::string::npos) return false;
  JsonDocument doc;
  if (deserializeJson(doc, json)) return false;
  if (doc["type"] == "_tap") {
    hal::inject_tap(doc["x"] | 0, doc["y"] | 0);
    Serial.printf("rx: tap %d,%d\n", (int)(doc["x"] | 0), (int)(doc["y"] | 0));
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

static void on_message(const std::string& json) {
  if (local_command(json)) return;
  app::Incoming in = app::parse(json);
  switch (in.type) {
    case app::MessageType::State:
      ui::apply(in.state, true);
      Serial.printf("rx: state sessions=%u pending=%u night=%d\n", (unsigned)in.state.sessions.size(),
                    (unsigned)in.state.pending.size(), in.state.night);
      break;
    case app::MessageType::Joke:
      ui::on_joke(in.text);
      Serial.println("rx: joke");
      break;
    default:
      break;
  }
}

void setup() {
  // Hub state messages run to a few KB; the default 256-byte RX buffer overflows
  // while LVGL is rendering and the JSON arrives truncated.
  Serial.setRxBufferSize(8192);
  Serial.begin(115200);
  hal::lvgl_begin();
  ui::begin(send_to_hub);
  net::serial_begin(on_message);
  net::hub_begin(net::load_config(), on_message, on_status);
  Serial.println("buddy: ready");
}

void loop() {
  net::serial_loop();
  net::hub_loop();
  static uint32_t battery_at = 0;
  if (hal::board::kHasBattery && (battery_at == 0 || millis() - battery_at > 30000)) {
    battery_at = millis();
    ui::set_battery(hal::board::battery_percent(), hal::board::charging());
  }
  ui::loop();
  hal::lvgl_loop();
  delay(5);
}
