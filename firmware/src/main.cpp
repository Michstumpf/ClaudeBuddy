// Claude Buddy firmware entry point: wires the link (serial for now; the
// WebSocket to the hub comes next), the protocol and the UI.
#include <Arduino.h>
#include <ArduinoJson.h>

#include "app/protocol.h"
#include "hal/lvgl_port.h"
#include "net/serial_link.h"
#include "ui/ui.h"

// Test-only commands from Wokwi scenarios (never sent by the hub).
static bool test_command(const std::string& json) {
  if (json.find("\"_tap\"") == std::string::npos) return false;
  JsonDocument doc;
  if (deserializeJson(doc, json) || doc["type"] != "_tap") return false;
  hal::inject_tap(doc["x"] | 0, doc["y"] | 0);
  Serial.printf("rx: tap %d,%d\n", (int)(doc["x"] | 0), (int)(doc["y"] | 0));
  return true;
}

static void on_message(const std::string& json) {
  if (test_command(json)) return;
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
  ui::begin(net::serial_send);
  net::serial_begin(on_message);
  Serial.println("buddy: ready");
}

void loop() {
  net::serial_loop();
  ui::loop();
  hal::lvgl_loop();
  delay(5);
}
