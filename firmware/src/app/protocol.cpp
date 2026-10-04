#include "protocol.h"

#include <ArduinoJson.h>

namespace app {

static Status status_of(const char* s) {
  if (!s) return Status::Idle;
  std::string v(s);
  if (v == "working") return Status::Working;
  if (v == "waiting") return Status::Waiting;
  if (v == "done") return Status::Done;
  if (v == "offline") return Status::Offline;
  return Status::Idle;
}

static std::string str(JsonVariantConst v) { return v.is<const char*>() ? std::string(v.as<const char*>()) : std::string(); }

Incoming parse(const std::string& json) {
  Incoming in;
  JsonDocument doc;
  if (deserializeJson(doc, json)) return in;
  const std::string type = str(doc["type"]);

  if (type == "state") {
    in.type = MessageType::State;
    State& st = in.state;
    for (JsonObjectConst s : doc["sessions"].as<JsonArrayConst>()) {
      Session x;
      x.id = str(s["id"]);
      x.name = str(s["name"]);
      x.host = str(s["host"]);
      x.last_message = str(s["last_message"]);
      x.status = status_of(s["status"]);
      x.has_tmux = s["tmux_pane"].is<const char*>();
      st.sessions.push_back(x);
    }
    for (JsonObjectConst a : doc["pending"].as<JsonArrayConst>()) {
      Approval x;
      x.id = str(a["id"]);
      x.session_name = str(a["session_name"]);
      x.tool_name = str(a["tool_name"]);
      x.summary = str(a["summary"]);
      x.dangerous = a["dangerous"] | false;
      x.expires_at = a["expires_at"] | 0.0;
      x.expires_in = a["expires_in"] | 20.0f;  // older hubs: assume the default timeout
      st.pending.push_back(x);
    }
    st.devices = doc["devices"] | 0;
    st.night = doc["night"] | false;
    JsonObjectConst set = doc["settings"];
    if (!set.isNull()) {
      st.settings.jokes = set["jokes"] | true;
      st.settings.joke_voice = set["joke_voice"] | true;
      st.settings.weather = set["weather"] | true;
      st.settings.joke_interval_min = set["joke_interval_min"] | 45;
      // The hub's search result wins over what was typed ("porto alegre").
      std::string city = str(set["city_geo"]["name"]);
      if (city.empty()) city = str(set["city"]);
      if (!city.empty()) st.settings.city = city;
      st.settings.eyes_skin = str(set["skin"]) == "eyes";
      st.settings.show_battery = set["battery"] | true;
      st.settings.long_task_min = set["long_task_min"] | 15;
    }
    JsonObjectConst w = doc["weather"];
    if (!w.isNull()) {
      st.weather.valid = true;
      st.weather.temp = w["temp"] | 0;
      st.weather.place = str(w["place"]);
      st.weather.text = str(w["text"]);
    }
    JsonObjectConst pomo = doc["pomodoro"];
    if (!pomo.isNull()) {
      st.pomodoro.phase = str(pomo["phase"]);
      st.pomodoro.ends_in = pomo["ends_in"] | 0;
    }
    st.focus = str(doc["focus"]);
    st.github_reviews = doc["github"]["reviews"] | 0;
    st.github_failing = doc["github"]["failing"] | 0;
    JsonObjectConst cal = doc["calendar"];
    if (!cal.isNull()) {
      st.in_meeting = cal["now"].is<const char*>();
      st.next_meeting = str(cal["next"]["title"]);
      st.next_meeting_in = cal["next"]["starts_in"] | -1;
    }
    JsonObjectConst ev = doc["event"];
    if (!ev.isNull()) {
      st.event.kind = str(ev["kind"]);
      st.event.session = str(ev["session"]);
    }
  } else if (type == "speech" || type == "joke" || type == "notice") {
    in.type = type == "joke" ? MessageType::Joke : type == "notice" ? MessageType::Notice : MessageType::Speech;
    in.text = str(doc["text"]);
    in.url = str(doc["url"]);
    in.id = str(doc["id"]);
  } else if (type == "decision_result" || type == "dictate_result") {
    in.type = type == "decision_result" ? MessageType::DecisionResult : MessageType::DictateResult;
    in.id = str(doc["id"]);
    in.ok = doc["ok"] | false;
  } else if (type == "pong") {
    in.type = MessageType::Pong;
  }
  return in;
}

static std::string dump(JsonDocument& doc) {
  std::string out;
  serializeJson(doc, out);
  return out;
}

std::string decision(const std::string& id, bool allow) {
  JsonDocument doc;
  doc["type"] = "decision";
  doc["id"] = id;
  doc["behavior"] = allow ? "allow" : "deny";
  doc["via"] = "touch";
  return dump(doc);
}

std::string touch() { return R"({"type":"touch"})"; }
std::string joke_now() { return R"({"type":"joke_now"})"; }
std::string ping() { return R"({"type":"ping"})"; }

std::string battery(int percent, bool charging) {
  JsonDocument doc;
  doc["type"] = "battery";
  doc["percent"] = percent;
  doc["charging"] = charging;
  return dump(doc);
}

std::string pomodoro(bool start) {
  return start ? R"({"type":"pomodoro","action":"start"})" : R"({"type":"pomodoro","action":"stop"})";
}

std::string settings(const Settings& s) {
  JsonDocument doc;
  doc["type"] = "settings";
  JsonObject v = doc["values"].to<JsonObject>();
  v["jokes"] = s.jokes;
  v["joke_voice"] = s.joke_voice;
  v["weather"] = s.weather;
  v["joke_interval_min"] = s.joke_interval_min;
  v["skin"] = s.eyes_skin ? "eyes" : "classic";
  v["battery"] = s.show_battery;
  v["long_task_min"] = s.long_task_min;
  return dump(doc);
}

}  // namespace app
