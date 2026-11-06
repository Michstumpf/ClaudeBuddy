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
void on_shake();  // the Buddy was shaken: dizzy until the joke arrives (at most 3 s)
void set_battery(int percent, bool charging);  // percent < 0: no battery, hide it
// Link status: while not connected the face says why ("conectando ao WiFi…").
void set_link(const char* status, bool connected);
// ⚙ → "WiFi e hub" → Configurar calls this (main opens the setup portal).
void set_setup_handler(void (*handler)());
// A full-screen two-line message (setup portal instructions), drawn right away.
void show_message(const char* line1, const char* line2);
// Push-to-talk: the board has a mic. "Ditar" in a session view (hold, speak,
// release) and holding the face (voice commands) call these.
void set_dictation_handlers(void (*start)(), void (*stop)(const std::string& session_id));
void set_listening(bool on);  // the face says "ouvindo… solte para enviar"
const app::Settings& settings();  // the latest settings from the hub

// The Buddy's own numbers, for Ajustes → Sistema (main sends them every second).
struct Stats {
  int cpu = 0;                          // % of each second the loop spent working (not idle)
  uint32_t heap_free = 0, heap_total = 0, psram_free = 0, psram_total = 0;
  uint32_t firmware = 0, firmware_max = 0;  // bytes of flash used by the firmware / its partition
  int rssi = 0;                         // WiFi signal, dBm (0: not connected)
  std::string ip;
  uint32_t uptime_s = 0;
};
void set_stats(const Stats& stats);

// The 🎤 flow (Ajustes → Som e tela → Microfone). main records and transcribes.
struct MicHandlers {
  bool (*start)() = nullptr;          // start recording; false: no mic
  int (*level)() = nullptr;           // 0..100, for the live wave
  float (*seconds)() = nullptr;       // recorded so far
  void (*finish)(bool send) = nullptr;  // stop; if send, transcribe and call show_transcript
};
void set_mic_handlers(const MicHandlers& handlers);

// Short press on the power button: Dormir / Reiniciar / Desligar / Cancelar.
// Pressing it again (or 10 s without a choice) closes the window.
struct PowerHandlers {
  void (*sleep)() = nullptr;
  void (*restart)() = nullptr;
  void (*off)() = nullptr;
};
void set_power_handlers(const PowerHandlers& handlers);
void toggle_power_menu();
void show_transcript(bool ok, const std::string& text);  // text, or why it failed
void on_send_result(bool ok, const std::string& detail);  // dictate_result / command_result
void loop();  // animations and timeouts; call often

}  // namespace ui
