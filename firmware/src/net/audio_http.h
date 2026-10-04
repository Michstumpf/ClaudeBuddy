// Audio over plain HTTP to the hub: send a recording to /api/transcribe, and
// download spoken replies (/api/speech/<id>). Blocking (1-3 s); the screen
// shows what is going on meanwhile.
#pragma once
#include <stdint.h>

#include <string>
#include <vector>

#include "hub_link.h"

namespace net {

struct Transcription {
  bool ok = false;
  bool handled = false;  // a voice intent the hub took care of ("manda para a HIPAA: …")
  std::string text, detail;
};

Transcription transcribe(const HubConfig& cfg, const int16_t* pcm, size_t samples, uint32_t rate = 16000);
bool download(const HubConfig& cfg, const std::string& path, std::vector<uint8_t>& out);

}  // namespace net
