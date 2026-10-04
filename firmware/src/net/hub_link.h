// The real link: WiFi + the hub's WebSocket (/ws?token=...), same messages as
// the serial test link. Reconnects on its own; never blocks the UI.
#pragma once
#include <string>

namespace net {

struct HubConfig {
  std::string ssid, password, host, token;
  uint16_t port = 8765;
};

// Saved in NVS (flash); falls back to the build's defaults (secrets.ini).
HubConfig load_config();
void save_config(const HubConfig& cfg);

using MessageHandler = void (*)(const std::string& json);
// Called with a short status for the screen ("conectando ao WiFi…") and
// whether the hub is connected.
using StatusHandler = void (*)(const char* status, bool connected);

void hub_begin(const HubConfig& cfg, MessageHandler on_message, StatusHandler on_status);
void hub_loop();
bool hub_send(const std::string& json);  // false when not connected

// Setup over the phone: opens the WiFi network "Buddy-setup" with a page to
// pick the WiFi and enter the hub address and token, saves them and restarts.
// Blocks until done; on_screen is told what to show meanwhile.
void run_setup_portal(void (*on_screen)(const char* line1, const char* line2));
bool configured();  // WiFi and hub set (otherwise the portal opens at boot)

}  // namespace net
