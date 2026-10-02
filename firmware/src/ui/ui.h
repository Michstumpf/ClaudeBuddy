// The Buddy's screens, in LVGL. Mirrors simulator/index.html, which stays the
// reference for behavior and look.
#pragma once
#include <string>

#include "../app/state.h"

namespace ui {

using Sender = void (*)(const std::string& json);

void begin(Sender send);
void apply(const app::State& state, bool connected);  // every state message
void on_joke(const std::string& text);
void set_battery(int percent, bool charging);  // percent < 0: no battery, hide it
void loop();  // animations and timeouts; call often

}  // namespace ui
