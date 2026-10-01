// Host tests for the protocol and the face logic, with messages shaped
// exactly like the hub's (see hub/buddy_hub/state.py snapshot()).
#include <unity.h>

#include "../../src/app/mood.h"
#include "../../src/app/protocol.h"

using namespace app;

static const char* kState = R"({
  "type": "state",
  "sessions": [
    {"id": "a", "name": "DataHub Sharing Chat", "host": "dell", "status": "working",
     "last_message": "Feito: 38 testes passaram.", "tmux_pane": "%3", "permission_mode": "default",
     "updated_at": 1.0, "title": null, "voice_turn": false},
    {"id": "b", "name": "git-d6", "host": "dell", "status": "idle", "last_message": "", "tmux_pane": null}
  ],
  "pending": [
    {"id": "p1", "session_id": "a", "session_name": "DataHub Sharing Chat", "tool_name": "Bash",
     "summary": "git push --force origin main", "dangerous": true, "expires_at": 1790000000.5}
  ],
  "devices": 2, "night": false,
  "settings": {"jokes": false, "joke_voice": true, "joke_interval_min": 30, "weather": true},
  "weather": {"place": "Canoas", "temp": 17, "min": 12, "max": 18, "text": "nublado", "icon": "☁", "updated_at": 1.0},
  "event": {"kind": "approval", "session": "DataHub Sharing Chat", "id": "p1"}
})";

void test_parses_full_state() {
  Incoming in = parse(kState);
  TEST_ASSERT_TRUE(in.type == MessageType::State);
  const State& st = in.state;
  TEST_ASSERT_EQUAL(2, st.sessions.size());
  TEST_ASSERT_EQUAL_STRING("DataHub Sharing Chat", st.sessions[0].name.c_str());
  TEST_ASSERT_TRUE(st.sessions[0].status == Status::Working);
  TEST_ASSERT_TRUE(st.sessions[0].has_tmux);
  TEST_ASSERT_FALSE(st.sessions[1].has_tmux);  // "tmux_pane": null
  TEST_ASSERT_EQUAL(1, st.pending.size());
  TEST_ASSERT_TRUE(st.pending[0].dangerous);
  TEST_ASSERT_EQUAL_STRING("git push --force origin main", st.pending[0].summary.c_str());
  TEST_ASSERT_EQUAL(2, st.devices);
  TEST_ASSERT_FALSE(st.settings.jokes);
  TEST_ASSERT_EQUAL(30, st.settings.joke_interval_min);
  TEST_ASSERT_TRUE(st.weather.valid);
  TEST_ASSERT_EQUAL(17, st.weather.temp);
  TEST_ASSERT_EQUAL_STRING("nublado", st.weather.text.c_str());
  TEST_ASSERT_EQUAL_STRING("approval", st.event.kind.c_str());
}

void test_old_hub_without_new_fields() {
  Incoming in = parse(R"({"type":"state","sessions":[],"pending":[],"devices":1})");
  TEST_ASSERT_TRUE(in.type == MessageType::State);
  TEST_ASSERT_FALSE(in.state.weather.valid);
  TEST_ASSERT_TRUE(in.state.settings.jokes);  // defaults
  TEST_ASSERT_EQUAL_STRING("", in.state.event.kind.c_str());
}

void test_joke_speech_and_garbage() {
  Incoming j = parse(R"({"type":"joke","text":"O café eventualmente termina!","url":"/api/speech/ab12"})");
  TEST_ASSERT_TRUE(j.type == MessageType::Joke);
  TEST_ASSERT_EQUAL_STRING("/api/speech/ab12", j.url.c_str());
  Incoming s = parse(R"({"type":"speech","id":"x","session":"api","text":"Feito.","url":"/api/speech/x"})");
  TEST_ASSERT_TRUE(s.type == MessageType::Speech);
  TEST_ASSERT_TRUE(parse("not json").type == MessageType::Unknown);
  TEST_ASSERT_TRUE(parse(R"({"type":"something_new"})").type == MessageType::Unknown);
}

void test_outgoing_messages() {
  TEST_ASSERT_EQUAL_STRING(R"({"type":"decision","id":"p1","behavior":"allow","via":"touch"})",
                           decision("p1", true).c_str());
  TEST_ASSERT_EQUAL_STRING(R"({"type":"decision","id":"p1","behavior":"deny","via":"touch"})",
                           decision("p1", false).c_str());
  Settings s;
  s.joke_voice = false;
  TEST_ASSERT_EQUAL_STRING(
      R"({"type":"settings","values":{"jokes":true,"joke_voice":false,"weather":true,"joke_interval_min":45}})",
      settings(s).c_str());
}

void test_mood_rules() {
  State st = parse(kState).state;
  TEST_ASSERT_TRUE(mood(st, false, false, "").mood == Mood::Off);
  TEST_ASSERT_TRUE(mood(st, true, false, "").mood == Mood::Wait);  // pending approval wins
  st.pending.clear();
  TEST_ASSERT_TRUE(mood(st, true, false, "").mood == Mood::Work);
  TEST_ASSERT_TRUE(mood(st, true, false, "api").mood == Mood::Done);
  TEST_ASSERT_TRUE(mood(st, true, true, "").mood == Mood::Talk);
  st.night = true;
  TEST_ASSERT_TRUE(mood(st, true, false, "").mood == Mood::Sleep);
  st.night = false;
  for (auto& s : st.sessions) s.status = Status::Idle;
  TEST_ASSERT_TRUE(mood(st, true, false, "").mood == Mood::Idle);
  st.sessions.clear();
  TEST_ASSERT_TRUE(mood(st, true, false, "").mood == Mood::Sleep);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_parses_full_state);
  RUN_TEST(test_old_hub_without_new_fields);
  RUN_TEST(test_joke_speech_and_garbage);
  RUN_TEST(test_outgoing_messages);
  RUN_TEST(test_mood_rules);
  return UNITY_END();
}
