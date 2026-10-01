#include "serial_link.h"

#include <Arduino.h>

namespace net {

static Handler handler_ = nullptr;
static std::string line_;

void serial_begin(Handler on_message) { handler_ = on_message; }

void serial_loop() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (!line_.empty() && line_[0] == '{' && handler_) handler_(line_);
      line_.clear();
    } else if (line_.size() < 8192) {
      line_ += c;
    }
  }
}

void serial_send(const std::string& json) { Serial.printf("tx: %s\n", json.c_str()); }

}  // namespace net
