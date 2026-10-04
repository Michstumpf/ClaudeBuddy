#include "audio_http.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

namespace net {

static void put32(uint8_t* p, uint32_t v) { memcpy(p, &v, 4); }
static void put16(uint8_t* p, uint16_t v) { memcpy(p, &v, 2); }

Transcription transcribe(const HubConfig& cfg, const int16_t* pcm, size_t samples, uint32_t rate) {
  Transcription t;
  if (WiFi.status() != WL_CONNECTED || cfg.host.empty() || !pcm || !samples) return t;
  const size_t data = samples * 2, total = 44 + data;
  auto* wav = static_cast<uint8_t*>(ps_malloc(total));
  if (!wav) return t;
  memcpy(wav, "RIFF", 4), put32(wav + 4, total - 8), memcpy(wav + 8, "WAVEfmt ", 8);
  put32(wav + 16, 16), put16(wav + 20, 1), put16(wav + 22, 1), put32(wav + 24, rate);
  put32(wav + 28, rate * 2), put16(wav + 32, 2), put16(wav + 34, 16), memcpy(wav + 36, "data", 4);
  put32(wav + 40, data);
  memcpy(wav + 44, pcm, data);

  HTTPClient http;
  http.setTimeout(60000);
  http.begin(cfg.host.c_str(), cfg.port, "/api/transcribe");
  http.addHeader("X-Buddy-Token", cfg.token.c_str());
  http.addHeader("Content-Type", "audio/wav");
  const int code = http.POST(wav, total);
  free(wav);
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      t.ok = true;
      t.handled = doc["handled"] | false;
      t.text = doc["text"] | "";
      t.detail = doc["detail"] | "";
    }
  } else {
    t.detail = "o hub respondeu " + std::to_string(code);
  }
  http.end();
  return t;
}

bool download(const HubConfig& cfg, const std::string& path, std::vector<uint8_t>& out) {
  if (WiFi.status() != WL_CONNECTED || cfg.host.empty()) return false;
  HTTPClient http;
  http.setTimeout(20000);
  http.begin(cfg.host.c_str(), cfg.port, (path + "?token=" + cfg.token).c_str());
  const int code = http.GET();
  bool ok = false;
  if (code == 200) {
    WiFiClient* stream = http.getStreamPtr();
    int left = http.getSize();  // -1: unknown (chunked)
    uint8_t chunk[1024];
    while (http.connected() && (left > 0 || left == -1)) {
      const size_t n = stream->readBytes(chunk, sizeof(chunk));
      if (!n) break;
      out.insert(out.end(), chunk, chunk + n);
      if (left > 0) left -= n;
    }
    ok = !out.empty();
  }
  http.end();
  return ok;
}

}  // namespace net
