#include "mood.h"

namespace app {

MoodView mood(const State& st, bool connected, bool talking, const std::string& done_session) {
  if (!connected) return {Mood::Off, "sem conexão com o hub"};
  if (talking) return {Mood::Talk, "falando…"};
  if (st.night && st.pending.empty()) return {Mood::Sleep, "boa noite"};
  bool any = false, waiting = false, working = false;
  for (const auto& s : st.sessions) {
    if (s.status == Status::Offline) continue;
    any = true;
    waiting |= s.status == Status::Waiting;
    working |= s.status == Status::Working;
  }
  if (waiting || !st.pending.empty()) return {Mood::Wait, "alguém precisa de você!"};
  if (!done_session.empty()) return {Mood::Done, done_session + " terminou!"};
  if (working) return {Mood::Work, ""};
  if (any) return {Mood::Idle, "tudo tranquilo"};
  return {Mood::Sleep, "zzz"};
}

}  // namespace app
