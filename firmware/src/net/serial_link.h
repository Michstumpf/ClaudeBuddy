// Test link: the hub's JSON messages, one per line, over the serial port.
// Lets Wokwi scenarios drive every screen without a network; messages the
// Buddy would send to the hub are printed as "tx: {...}".
#pragma once
#include <string>

namespace net {
using Handler = void (*)(const std::string& json);
void serial_begin(Handler on_message);
void serial_loop();
void serial_send(const std::string& json);
}  // namespace net
