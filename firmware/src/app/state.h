// What the Buddy knows, filled from the hub's {"type":"state"} messages.
// Plain C++ (no Arduino/LVGL) so it can be unit-tested on the host.
#pragma once
#include <stdint.h>

#include <string>
#include <vector>

namespace app {

enum class Status : uint8_t { Idle, Working, Waiting, Done, Offline };

struct Session {
  std::string id, name, host, last_message;
  Status status = Status::Idle;
  bool has_tmux = false;
};

struct Approval {
  std::string id, session_name, tool_name, summary;
  bool dangerous = false;
  double expires_at = 0;  // unix seconds (hub clock)
  float expires_in = 0;   // seconds left when the hub sent it (no clock sync needed)
};

struct Weather {
  bool valid = false;
  int temp = 0;
  std::string place, text;
};

struct Settings {
  bool jokes = true, joke_voice = true, weather = true;
  int joke_interval_min = 45;
};

struct Event {
  std::string kind, session;  // kind: done | attention | approval | night | morning | ""
};

struct State {
  std::vector<Session> sessions;
  std::vector<Approval> pending;
  int devices = 0;
  bool night = false;
  Settings settings;
  Weather weather;
  Event event;
};

}  // namespace app
