// The mascot as a pixel grid, identical to simulator/index.html (BODY/EYES):
// rebuilt from Claude Code's terminal logo; 1 unit = 10 screen pixels there.
#pragma once
#include <stdint.h>

namespace sprite {

constexpr int kCols = 18, kBodyRows = 8;
// '#' = body pixel.
constexpr const char* kBody[kBodyRows] = {
    "...############...",
    "...############...",
    "...############...",
    "...############...",
    ".################.",
    ".################.",
    "...############...",
    "...############...",
};
// Legs (x, y), each 1x2 units; set A and B alternate when walking.
constexpr int kLegsA[2][2] = {{4, 8}, {11, 8}};
constexpr int kLegsB[2][2] = {{6, 8}, {13, 8}};

struct Rect { int8_t x, y, w, h; };
struct Eyes { const Rect* rects; uint8_t count; };

constexpr Rect kOpen[] = {{5, 2, 1, 2}, {12, 2, 1, 2}};
constexpr Rect kWide[] = {{5, 1, 1, 3}, {12, 1, 1, 3}};
constexpr Rect kShut[] = {{5, 3, 1, 1}, {12, 3, 1, 1}};
constexpr Rect kSleep[] = {{4, 3, 2, 1}, {11, 3, 2, 1}};
constexpr Rect kHappy[] = {{4, 3, 1, 1}, {5, 2, 1, 1}, {6, 3, 1, 1}, {11, 3, 1, 1}, {12, 2, 1, 1}, {13, 3, 1, 1}};
constexpr Rect kDead[] = {{4, 1, 1, 1}, {6, 1, 1, 1}, {5, 2, 1, 1}, {4, 3, 1, 1}, {6, 3, 1, 1},
                          {11, 1, 1, 1}, {13, 1, 1, 1}, {12, 2, 1, 1}, {11, 3, 1, 1}, {13, 3, 1, 1}};

constexpr Eyes kEyesOpen{kOpen, 2}, kEyesWide{kWide, 2}, kEyesShut{kShut, 2},
    kEyesSleep{kSleep, 2}, kEyesHappy{kHappy, 6}, kEyesDead{kDead, 10};

}  // namespace sprite
