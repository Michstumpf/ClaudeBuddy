// Which face to show, same rules as the browser simulator's mood().
#pragma once
#include <string>

#include "state.h"

namespace app {

enum class Mood : uint8_t { Off, Talk, Sleep, Wait, Done, Work, Idle };

struct MoodView {
  Mood mood;
  std::string label;  // empty for Work: the spinner draws its own label
};

// done_session: session of the last "done" event if it happened < 8 s ago.
MoodView mood(const State& st, bool connected, bool talking, const std::string& done_session);

}  // namespace app
