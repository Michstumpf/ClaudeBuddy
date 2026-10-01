// Claude Buddy firmware entry point: wires the link (serial for now; the
// WebSocket to the hub comes next), the protocol and the UI.
#include <Arduino.h>

#include "app/protocol.h"
#include "hal/lvgl_port.h"
#include "net/serial_link.h"
#include "ui/ui.h"

static void on_message(const std::string& json) {
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
