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
void on_notice(const std::string& text);  // hub notices: same bubble as jokes
void set_battery(int percent, bool charging);  // percent < 0: no battery, hide it
// Link status: while not connected the face says why ("conectando ao WiFi…").
void set_link(const char* status, bool connected);
// ⚙ → "WiFi e hub" → Configurar calls this (main opens the setup portal).
void set_setup_handler(void (*handler)());
// A full-screen two-line message (setup portal instructions), drawn right away.
void show_message(const char* line1, const char* line2);
void loop();  // animations and timeouts; call often

}  // namespace ui
