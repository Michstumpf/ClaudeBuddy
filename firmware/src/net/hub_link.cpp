#include "hub_link.h"

#include <Arduino.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

// Build-time defaults (secrets.ini, kept out of git; see secrets.ini.example).
#ifndef BUDDY_WIFI_SSID
#define BUDDY_WIFI_SSID ""
#endif
#ifndef BUDDY_WIFI_PASSWORD
#define BUDDY_WIFI_PASSWORD ""
#endif
#ifndef BUDDY_HUB_HOST
#define BUDDY_HUB_HOST ""
#endif
#ifndef BUDDY_HUB_PORT
#define BUDDY_HUB_PORT 8765
#endif
#ifndef BUDDY_HUB_TOKEN
#define BUDDY_HUB_TOKEN ""
#endif

namespace net {
namespace {

WebSocketsClient ws;
HubConfig cfg_;
MessageHandler on_message_ = nullptr;
StatusHandler on_status_ = nullptr;
enum class Phase { Off, Wifi, Hub, Connected } phase_ = Phase::Off;
uint32_t wifi_started_ = 0;

void status(const char* text, bool connected) {
  if (on_status_) on_status_(text, connected);
}

void on_ws_event(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      phase_ = Phase::Connected;
      Serial.println("hub: connected");
      status("conectado", true);
      break;
    case WStype_DISCONNECTED:
      if (phase_ == Phase::Connected) Serial.println("hub: disconnected");
      phase_ = Phase::Hub;
      status("procurando o hub…", false);
      break;
    case WStype_TEXT:
      if (on_message_) on_message_(std::string(reinterpret_cast<char*>(payload), length));
      break;
    default:
      break;
  }
}

}  // namespace

HubConfig load_config() {
  HubConfig c;
  Preferences nvs;
  nvs.begin("buddy", true);
  c.ssid = nvs.getString("ssid", BUDDY_WIFI_SSID).c_str();
  c.password = nvs.getString("pass", BUDDY_WIFI_PASSWORD).c_str();
  c.host = nvs.getString("host", BUDDY_HUB_HOST).c_str();
  c.port = nvs.getUShort("port", BUDDY_HUB_PORT);
  c.token = nvs.getString("token", BUDDY_HUB_TOKEN).c_str();
  nvs.end();
  return c;
}

void save_config(const HubConfig& c) {
  Preferences nvs;
  nvs.begin("buddy", false);
  nvs.putString("ssid", c.ssid.c_str());
  nvs.putString("pass", c.password.c_str());
  nvs.putString("host", c.host.c_str());
  nvs.putUShort("port", c.port);
  nvs.putString("token", c.token.c_str());
  nvs.end();
}

void hub_begin(const HubConfig& cfg, MessageHandler on_message, StatusHandler on_status) {
  cfg_ = cfg;
  on_message_ = on_message;
  on_status_ = on_status;
  if (cfg_.ssid.empty() || cfg_.host.empty()) {
    Serial.println("hub: no WiFi/hub configured (serial link only)");
    status("sem WiFi configurado", false);
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(cfg_.ssid.c_str(), cfg_.password.c_str());
  wifi_started_ = millis();
  phase_ = Phase::Wifi;
  Serial.printf("hub: joining WiFi \"%s\"\n", cfg_.ssid.c_str());
  status("conectando ao WiFi…", false);
}

void hub_loop() {
  switch (phase_) {
    case Phase::Off:
      return;
    case Phase::Wifi:
      if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("hub: WiFi up (%s), hub at %s:%u\n", WiFi.localIP().toString().c_str(), cfg_.host.c_str(),
                      cfg_.port);
        const std::string path = "/ws?token=" + cfg_.token;
        ws.begin(cfg_.host.c_str(), cfg_.port, path.c_str());
        ws.onEvent(on_ws_event);
        ws.setReconnectInterval(3000);
        ws.enableHeartbeat(15000, 3000, 2);  // notices a hub that vanished without closing
        phase_ = Phase::Hub;
        status("procurando o hub…", false);
      } else if (millis() - wifi_started_ > 20000) {
        WiFi.reconnect();  // keep trying; the screen says why it is offline
        wifi_started_ = millis();
      }
      return;
    case Phase::Hub:
    case Phase::Connected:
      if (WiFi.status() != WL_CONNECTED) {
        phase_ = Phase::Wifi;
        wifi_started_ = millis();
        status("WiFi caiu, reconectando…", false);
        return;
      }
      ws.loop();
      return;
  }
}

bool hub_send(const std::string& json) {
  if (phase_ != Phase::Connected) return false;
  return ws.sendTXT(json.c_str(), json.size());
}

}  // namespace net
