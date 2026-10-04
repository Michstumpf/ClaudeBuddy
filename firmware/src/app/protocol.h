// The hub <-> Buddy WebSocket protocol (README "Protocolo do dispositivo").
// Pure C++ + ArduinoJson, unit-tested in the native env.
#pragma once
#include <string>

#include "state.h"

namespace app {

enum class MessageType { Unknown, State, Speech, Joke, DecisionResult, DictateResult, Pong };

struct Incoming {
  MessageType type = MessageType::Unknown;
  State state;                  // for State
  std::string text, url, id;    // for Speech / Joke / results
  bool ok = false;              // for results
};

// Parses one JSON message from the hub. Unknown fields are ignored, missing
// ones keep their defaults, so the hub can grow the protocol safely.
Incoming parse(const std::string& json);

std::string decision(const std::string& id, bool allow);  // always via "touch"
std::string touch();
std::string settings(const Settings& s);
std::string joke_now();
std::string ping();
std::string battery(int percent, bool charging);

}  // namespace app
