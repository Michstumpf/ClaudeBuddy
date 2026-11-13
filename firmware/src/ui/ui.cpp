#include "ui.h"

#include <Arduino.h>
#include <lvgl.h>

#include <vector>

#include "../app/mood.h"
#include "../hal/board.h"
#include "../app/protocol.h"
#include "sprite.h"

LV_FONT_DECLARE(buddy_font_11);
LV_FONT_DECLARE(buddy_font_13);
LV_FONT_DECLARE(buddy_font_48);

namespace ui {
namespace {

constexpr int kUnit = 10;               // 1 sprite unit = 10 px, like the simulator
constexpr int kSpriteX = 70, kSpriteY = 52;  // where the body's top-left corner sits
// The sprite's box has room around the body for the headphones' band (above),
// the coffee mug (right) and the eyes looking around: LVGL does not draw a
// child outside its parent, which is how the band went missing on the CoreS3.
constexpr int kPad = 3 * kUnit, kCanvasW = 25 * kUnit, kCanvasH = 13 * kUnit;
const lv_color_t kBg = lv_color_hex(0x0e0f12), kFg = lv_color_hex(0xf2efe9), kMuted = lv_color_hex(0xb9b4ad),
                 kAccent = lv_color_hex(0xd97757), kOff = lv_color_hex(0x5a5550), kWarn = lv_color_hex(0xe8b04a),
                 kOk = lv_color_hex(0x4caf7a), kBar = lv_color_hex(0x1b1d22);

Sender send_ = nullptr;
app::State state_;
bool connected_ = false;

enum View : uint8_t { kFace, kList, kSession, kApprove, kSettings, kMenu, kPomo, kRec, kPick, kViews };
lv_obj_t* views_[kViews];
View view_ = kFace;
// Taps right after the screen changed are ignored: a finger already down when
// an approval pops up must not land on "Aprovar" without the user seeing it.
constexpr uint32_t kSettleMs = 400;
uint32_t view_changed_at_ = 0;
bool settling() { return millis() - view_changed_at_ < kSettleMs; }
std::string current_session_;           // id shown in kSession
std::string approval_id_;               // pending approval on screen
uint32_t approval_seen_ = 0;            // millis() when it arrived
float approval_secs_ = 20;              // seconds left when it arrived
constexpr uint32_t kIdleBackMs = 30000;
constexpr uint32_t kMenuIdleMs = 10000;  // the menu is a quick stop

const lv_color_t kBox = lv_color_hex(0x181a1f), kBtn = lv_color_hex(0x3a3f48), kBtnOk = lv_color_hex(0x2f8a5b),
                 kBtnBad = lv_color_hex(0xb8423b), kBtnInfo = lv_color_hex(0x3c6fc4), kBad = lv_color_hex(0xe0574f),
                 kInfo = lv_color_hex(0x6aa8ff);
lv_obj_t *list_count_, *list_rows_, *sess_title_, *sess_msg_, *appr_timer_, *appr_title_, *appr_danger_, *appr_body_;
constexpr int kPrefs = 14;
// jokes, joke_voice, interval, weather, skin, battery, long task, pomodoro length, wifi, volume, brightness, mic,
// flat sleep, face-down sleep
lv_obj_t* pref_btn_[kPrefs];
void (*setup_handler_)() = nullptr;
void (*dictate_start_)() = nullptr;
void (*dictate_stop_)(const std::string&) = nullptr;
bool listening_ = false;
lv_obj_t* dictate_btn_ = nullptr;
lv_obj_t* bar_mid_;           // pomodoro countdown
uint32_t pomo_at_ = 0;        // millis() when the countdown was received
int pomo_secs_ = 0;
lv_obj_t* weather_label_;  // "Temperatura em <cidade>"

lv_obj_t *face_, *sprite_, *label_, *weather_, *bubble_, *bubble_text_, *bar_left_, *bar_right_;
lv_obj_t *spin_row_ = nullptr, *glyph_ = nullptr, *verb_ = nullptr;
lv_obj_t *fx_alert_, *fx_zzz_[3], *fx_spark_[2], *fx_wave_[4];
uint32_t mood_since_ = 0;  // millis() when the mood last changed (Done hops 3 times)
std::vector<lv_obj_t*> body_, legs_a_, legs_b_, eyes_, big_eyes_;
lv_obj_t* eyes_layer_ = nullptr;  // holds eyes_, moved sideways when the mascot looks around
// Skin "eyes": only the eyes, big, on a body-colored screen (for a case shaped
// like the mascot). Same eye shapes, 24 px units, sprite columns 3..14.
constexpr int kBigUnit = 24, kBigX = 16, kBigY = 34;
lv_obj_t* bigeyes_ = nullptr;
bool eyes_skin_ = false;
// Pomodoro look, like the simulator: headphones and music notes while focusing,
// a coffee with steam on the break, and a thin progress bar under the mascot.
std::vector<lv_obj_t*> phones_, mug_;
lv_obj_t *fx_notes_[2], *fx_steam_[2], *pomo_track_, *pomo_mini_;
std::string face_phase_;  // pomodoro phase the face is drawn for
// Shaken: dizzy (spiral eyes, wobbling, stars around the head) until the joke arrives.
uint32_t dizzy_until_ = 0;
MicHandlers mic_;             // the 🎤 flow, see set_mic_handlers
PowerHandlers power_;         // the power button's window
lv_obj_t* power_menu_ = nullptr;
uint32_t power_menu_at_ = 0;
lv_obj_t* mic_btn_ = nullptr;
lv_obj_t* fx_stars_[3];
// Idle blink, like the simulator: eyes shut for 140 ms every 3.5 s.
uint32_t blink_at_ = 0;
bool blinking_ = false;

app::Mood mood_ = app::Mood::Off;
bool redraw_ = true;
int battery_ = -1;          // percent, -1 = no battery
bool charging_ = false;
lv_obj_t *batt_shell_, *batt_fill_, *batt_bolt_;  // redraw the face even if the mood is the same (skin changed, first frame)
std::string link_status_;
std::string done_session_;
uint32_t done_at_ = 0, bubble_until_ = 0, frame_at_ = 0;
int frame_ = 0;

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h, lv_color_t color) {
  lv_obj_t* o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_set_pos(o, x, y);
  lv_obj_set_size(o, w, h);
  lv_obj_set_style_bg_color(o, color, 0);
  lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
  lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
  return o;
}

lv_obj_t* text(lv_obj_t* parent, const lv_font_t* font, lv_color_t color) {
  lv_obj_t* l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, color, 0);
  return l;
}

void build_sprite() {
  sprite_ = lv_obj_create(face_);
  lv_obj_remove_style_all(sprite_);
  lv_obj_set_size(sprite_, kCanvasW, kCanvasH);
  lv_obj_set_pos(sprite_, kSpriteX - kPad, kSpriteY - kPad);
  lv_obj_remove_flag(sprite_, LV_OBJ_FLAG_CLICKABLE);
  for (int y = 0; y < sprite::kBodyRows; y++) {  // one rect per horizontal run
    const char* row = sprite::kBody[y];
    for (int x = 0; x < sprite::kCols;) {
      if (row[x] != '#') { x++; continue; }
      int end = x;
      while (end < sprite::kCols && row[end] == '#') end++;
      body_.push_back(box(sprite_, kPad + x * kUnit, kPad + y * kUnit, (end - x) * kUnit, kUnit, kAccent));
      x = end;
    }
  }
  for (auto& l : sprite::kLegsA) legs_a_.push_back(box(sprite_, kPad + l[0] * kUnit, kPad + l[1] * kUnit, kUnit, 2 * kUnit, kAccent));
  for (auto& l : sprite::kLegsB) legs_b_.push_back(box(sprite_, kPad + l[0] * kUnit, kPad + l[1] * kUnit, kUnit, 2 * kUnit, kAccent));
  eyes_layer_ = lv_obj_create(sprite_);
  lv_obj_remove_style_all(eyes_layer_);
  lv_obj_set_pos(eyes_layer_, kPad, kPad);
  lv_obj_set_size(eyes_layer_, sprite::kCols * kUnit, 5 * kUnit);
  lv_obj_remove_flag(eyes_layer_, LV_OBJ_FLAG_CLICKABLE);
}

void draw_eyes(const sprite::Eyes& e) {
  for (auto* o : eyes_) lv_obj_delete(o);
  for (auto* o : big_eyes_) lv_obj_delete(o);
  eyes_.clear();
  big_eyes_.clear();
  for (int i = 0; i < e.count; i++) {
    const auto& r = e.rects[i];
    eyes_.push_back(box(eyes_layer_, r.x * kUnit, r.y * kUnit, r.w * kUnit, r.h * kUnit, kBg));
    big_eyes_.push_back(box(bigeyes_, (r.x - 3) * kBigUnit, r.y * kBigUnit, r.w * kBigUnit, r.h * kBigUnit, kBg));
  }
}

lv_color_t skin_background(app::Mood m) {
  switch (m) {
    case app::Mood::Wait: return kWarn;
    case app::Mood::Done: return kOk;
    case app::Mood::Off: return kOff;
    case app::Mood::Sleep: return lv_color_hex(0xb0603f);
    default: return kAccent;
  }
}

void apply_skin_colors() {
  if (eyes_skin_) {
    lv_obj_set_style_bg_color(face_, skin_background(mood_), 0);
    lv_obj_set_style_bg_opa(face_, LV_OPA_COVER, 0);
  } else {
    lv_obj_set_style_bg_opa(face_, LV_OPA_TRANSP, 0);
  }
  const lv_color_t text_color = eyes_skin_ ? kBar : kMuted;
  lv_obj_set_style_text_color(label_, text_color, 0);
  lv_obj_set_style_text_color(weather_, text_color, 0);
  if (verb_) lv_obj_set_style_text_color(verb_, text_color, 0);
  if (glyph_) lv_obj_set_style_text_color(glyph_, eyes_skin_ ? kBar : kAccent, 0);
}

const sprite::Eyes& eyes_for(app::Mood m) {
  switch (m) {
    case app::Mood::Wait: return sprite::kEyesWide;
    case app::Mood::Done: return sprite::kEyesHappy;
    case app::Mood::Sleep: return sprite::kEyesSleep;
    case app::Mood::Off: return sprite::kEyesDead;
    case app::Mood::Dizzy: return sprite::kEyesDizzy;
    default: return sprite::kEyesOpen;
  }
}

void color_sprite(lv_color_t c) {
  for (auto* o : body_) lv_obj_set_style_bg_color(o, c, 0);
  for (auto* o : legs_a_) lv_obj_set_style_bg_color(o, c, 0);
  for (auto* o : legs_b_) lv_obj_set_style_bg_color(o, c, 0);
}

// ---- Claude Code's spinner: "✶ Accomplishing…" ----
const char* kSpin[] = {"·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"};
const char* kVerbs[] = {"Accomplishing", "Baking", "Brewing", "Churning", "Clauding", "Cogitating", "Computing",
                        "Conjuring", "Crafting", "Crunching", "Deliberating", "Forging", "Hatching", "Herding",
                        "Hustling", "Marinating", "Moseying", "Mulling", "Musing", "Noodling", "Percolating",
                        "Pondering", "Reticulating", "Ruminating", "Simmering", "Spinning", "Stewing",
                        "Synthesizing", "Thinking", "Vibing", "Working"};
uint32_t verb_at_ = 0;
const char* verb_text_ = kVerbs[0];

void show_spinner(bool on) {
  if (on) lv_label_set_text(label_, "");  // the spinner takes the label's place
  if (on && !glyph_) {
    spin_row_ = lv_obj_create(face_);
    lv_obj_remove_style_all(spin_row_);
    lv_obj_set_size(spin_row_, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(spin_row_, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(spin_row_, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(spin_row_, 6, 0);
    lv_obj_align(spin_row_, LV_ALIGN_TOP_MID, 0, 174);
    glyph_ = text(spin_row_, &buddy_font_13, kAccent);
    verb_ = text(spin_row_, &buddy_font_11, kMuted);
    verb_text_ = kVerbs[random(sizeof(kVerbs) / sizeof(*kVerbs))];
    verb_at_ = millis();
  } else if (!on && glyph_) {
    lv_obj_delete(spin_row_);  // deletes glyph_ and verb_ too
    spin_row_ = glyph_ = verb_ = nullptr;
  }
}

void hide_bubble();
int pomo_left();

void show_if(lv_obj_t* o, bool on) {
  if (on) lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
}

void show_pomodoro_look() {
  const bool fx = !eyes_skin_ && connected_ && !state_.night;
  const bool focus = fx && face_phase_ == "focus", pause = fx && face_phase_ == "break";
  for (auto* o : phones_) show_if(o, focus);
  for (auto* o : fx_notes_) show_if(o, focus);
  for (auto* o : mug_) show_if(o, pause);
  for (auto* o : fx_steam_) show_if(o, pause);
  show_if(pomo_track_, focus || pause);
  const int len = state_.pomodoro.length;
  lv_obj_set_width(pomo_mini_, len > 0 ? 160 * (len - pomo_left()) / len : 0);
  lv_obj_set_style_bg_color(pomo_mini_, lv_color_hex(pause ? 0x6fc59a : 0xe8875f), 0);
}

void set_mood(const app::MoodView& v) {
  if (v.mood != mood_ || redraw_) {
    redraw_ = false;
    mood_ = v.mood;
    draw_eyes(mood_ == app::Mood::Idle && face_phase_ == "break" ? sprite::kEyesHappy : eyes_for(mood_));
    color_sprite(mood_ == app::Mood::Off ? kOff : kAccent);
    show_spinner(mood_ == app::Mood::Work);

    // The effects belong to the whole mascot; the eyes skin shows state by color.
    const bool fx = !eyes_skin_;
    mood_since_ = millis();
    for (auto* z : fx_zzz_) show_if(z, fx && mood_ == app::Mood::Sleep);
    for (auto* w : fx_wave_) {
      show_if(w, fx && mood_ == app::Mood::Talk);
      lv_obj_set_style_bg_color(w, listening_ ? kInfo : kAccent, 0);  // blue listening, body color talking
    }
    if (fx && mood_ == app::Mood::Wait) lv_obj_remove_flag(fx_alert_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(fx_alert_, LV_OBJ_FLAG_HIDDEN);
    for (auto* s : fx_spark_)
      if (fx && mood_ == app::Mood::Done) lv_obj_remove_flag(s, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(s, LV_OBJ_FLAG_HIDDEN);
    apply_skin_colors();
  }
  if (mood_ != app::Mood::Work) lv_label_set_text(label_, v.label.c_str());
}

void hide_bubble() {
  bubble_until_ = 0;
  lv_obj_add_flag(bubble_, LV_OBJ_FLAG_HIDDEN);

}

void set_skin(bool eyes) {
  if (eyes == eyes_skin_) return;
  eyes_skin_ = eyes;
  if (eyes) {
    lv_obj_add_flag(sprite_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(bigeyes_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_width(bubble_, 296);
    lv_obj_set_width(bubble_text_, 284);
    lv_obj_align(bubble_, LV_ALIGN_BOTTOM_MID, 0, -44);  // under the eyes, not on one of them
  } else {
    lv_obj_remove_flag(sprite_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(bigeyes_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_width(bubble_, 170);
    lv_obj_set_width(bubble_text_, 158);
    lv_obj_align(bubble_, LV_ALIGN_TOP_RIGHT, -8, 8);
  }
  redraw_ = true;
}

void refresh() {
  set_skin(state_.settings.eyes_skin);
  if (done_at_ && millis() - done_at_ > 8000) { done_at_ = 0; done_session_.clear(); }
  if (bubble_until_ && (state_.night || !connected_)) hide_bubble();
  app::MoodView v = app::mood(state_, connected_, false, done_session_);
  if (!connected_ && !link_status_.empty()) v.label = link_status_;
  if (battery_ >= 0 && battery_ <= 10 && !charging_ && (v.mood == app::Mood::Idle || v.mood == app::Mood::Sleep))
    v.label = "bateria fraca, me carrega?";
  if (dizzy_until_ && millis() < dizzy_until_ && connected_) v = {app::Mood::Dizzy, "uau, fiquei tonto…"};
  if (listening_) v = {app::Mood::Talk, "ouvindo… solte para enviar"};
  if (state_.pomodoro.phase != face_phase_) {
    face_phase_ = state_.pomodoro.phase;
    redraw_ = true;  // eyes: happy on the break
  }
  set_mood(v);
  show_pomodoro_look();

  // With the 🎤 button on, the temperature moves to the right corner.
  show_if(mic_btn_, state_.settings.mic && mic_.start && connected_);
  if (state_.settings.mic) lv_obj_align(weather_, LV_ALIGN_TOP_RIGHT, -8, 6);
  else lv_obj_set_pos(weather_, 8, 6);
  if (state_.weather.valid && state_.settings.weather)
    lv_label_set_text_fmt(weather_, "%d° %s", state_.weather.temp, state_.weather.place.c_str());
  else
    lv_label_set_text(weather_, "");

  int working = 0, waiting = 0, done = 0;
  for (const auto& s : state_.sessions) {
    working += s.status == app::Status::Working;
    waiting += s.status == app::Status::Waiting;
    done += s.status == app::Status::Done;
  }
  if (connected_) lv_label_set_text_fmt(bar_left_, "▶ %d  ‖ %d  ✓ %d", working, waiting, done);
  else lv_label_set_text(bar_left_, "offline");
}

// The simulator's CSS animations, same periods and curves (simulator/index.html is
// the reference): progress through a cycle is computed from the clock, so
// timing does not depend on how often this runs.
float cycle(uint32_t period_ms, uint32_t delay_ms = 0) {
  return static_cast<float>((millis() + period_ms - delay_ms % period_ms) % period_ms) / period_ms;
}
float ease(float t) { return t * t * (3 - 2 * t); }  // ease-in-out
// "hop": up 1.5 units at 40% of the cycle, eased both ways.
int hop(float p) { return -static_cast<int>(15 * ease(p < 0.4f ? p / 0.4f : 1 - (p - 0.4f) / 0.6f)); }
// "float": drifts 2 units right and 4 up, fading in over the first 20%, out after.
void float_fx(lv_obj_t* o, float p) {
  lv_obj_set_style_translate_x(o, static_cast<int>(20 * p), 0);
  lv_obj_set_style_translate_y(o, -static_cast<int>(40 * p), 0);
  lv_obj_set_style_opa(o, static_cast<lv_opa_t>(255 * (p < 0.2f ? p / 0.2f : 1 - (p - 0.2f) / 0.8f)), 0);
}

void animate() {
  if (millis() - frame_at_ < 50) return;  // ~20 fps
  frame_at_ = millis();
  static uint32_t spin_at = 0;
  if (glyph_ && millis() - spin_at >= 120) {
    spin_at = millis();
    frame_++;
    lv_label_set_text(glyph_, kSpin[frame_ % 10]);
    if (millis() - verb_at_ > 8000) { verb_text_ = kVerbs[random(sizeof(kVerbs) / sizeof(*kVerbs))]; verb_at_ = millis(); }
    lv_label_set_text_fmt(verb_, "%s…", verb_text_);
  }
  const bool focus = mood_ == app::Mood::Idle && face_phase_ == "focus";
  const bool dizzy = mood_ == app::Mood::Dizzy;
  int dx = 0, dy = 0;
  switch (mood_) {
    case app::Mood::Sleep: dy = static_cast<int>(3 * (0.5f - 0.5f * cosf(6.2832f * cycle(3200)))); break;  // breathe
    case app::Mood::Work: dy = cycle(500) < 0.5f ? 0 : -kUnit / 2; break;                                // bob
    case app::Mood::Talk: dy = listening_ || cycle(350) < 0.5f ? 0 : -kUnit / 2; break;  // listening: still
    case app::Mood::Wait: dy = hop(cycle(600)); break;
    case app::Mood::Done: dy = millis() - mood_since_ < 2700 ? hop(cycle(900, mood_since_)) : 0; break;  // 3 hops
    case app::Mood::Off: dy = kUnit; break;                                                               // slumped
    case app::Mood::Dizzy: {  // wobble side to side
      const float p = cycle(200);
      dx = static_cast<int>(-7 + 14 * (p < 0.5f ? p * 2 : 2 - p * 2));
      break;
    }
    default: break;
  }
  if (focus) dx = static_cast<int>(3 * sinf(6.2832f * cycle(1200)));  // grooves to the music
  if (bubble_until_ && !eyes_skin_) dx -= 34, dy += 14;             // steps aside for the bubble
  lv_obj_set_pos(sprite_, kSpriteX - kPad + dx, kSpriteY - kPad + dy);
  // Eyes skin: the big eyes bob while talking.
  lv_obj_set_y(bigeyes_, kBigY + (mood_ == app::Mood::Talk && cycle(350) >= 0.5f ? -kBigUnit / 2 : 0));

  // Legs walk while working (A and B alternate every half cycle).
  const bool half = cycle(500) >= 0.5f;
  for (auto* l : legs_a_) lv_obj_set_y(l, kPad + 8 * kUnit - (mood_ == app::Mood::Work && half ? kUnit : 0));
  for (auto* l : legs_b_) lv_obj_set_y(l, kPad + 8 * kUnit - (mood_ == app::Mood::Work && !half ? kUnit : 0));
  // "look": 0 until 25% of 2.4 s, left until 60%, right until the end.
  const float lp = cycle(2400);
  const int glance = mood_ == app::Mood::Work ? (lp < 0.25f ? 0 : lp < 0.6f ? -1 : 1) : 0;
  lv_obj_set_x(bigeyes_, kBigX + glance * kBigUnit);
  lv_obj_set_x(eyes_layer_, kPad + glance * kUnit);

  for (int i = 0; i < 3; i++) float_fx(fx_zzz_[i], cycle(3000, i * 1000));        // z z Z, 1 s apart
  for (int i = 0; i < 2; i++) float_fx(fx_notes_[i], cycle(2400, i * 1200));      // ♪ ♫
  for (int i = 0; i < 2; i++) float_fx(fx_steam_[i], cycle(2000, i * 1000));      // coffee steam
  lv_obj_set_style_opa(fx_alert_, cycle(600) < 0.5f ? LV_OPA_COVER : LV_OPA_0, 0);  // "!" flashes
  for (int i = 0; i < 2; i++)                                                       // sparks twinkle, 0.4 s apart
    lv_obj_set_style_opa(fx_spark_[i], cycle(800, i * 400) < 0.5f ? LV_OPA_COVER : LV_OPA_0, 0);
  // Sound bars beside the mascot: listening (slow) or talking (fast), alternating pairs.
  const uint32_t wave_ms = listening_ ? 1400 : 500;
  for (int i = 0; i < 4; i++) {
    const float p = cycle(wave_ms, i % 2 ? wave_ms / 4 : 0);
    const float scale = 0.4f + 0.9f * ease(p < 0.5f ? p * 2 : 2 - p * 2);
    const int base = i == 1 || i == 2 ? 40 : 30;
    const int h = static_cast<int>(base * scale);
    lv_obj_set_height(fx_wave_[i], h);
    lv_obj_set_y(fx_wave_[i], kPad + 5 * kUnit - h / 2);
  }
  // Dizzy: three stars circling the head, a full turn per second.
  for (int i = 0; i < 3; i++) {
    show_if(fx_stars_[i], dizzy && !eyes_skin_);
    if (!dizzy) continue;
    const float a = 6.2832f * cycle(1000, i * 333);
    lv_obj_set_pos(fx_stars_[i], kSpriteX + 84 + static_cast<int>(70 * cosf(a)), kSpriteY + 2 + static_cast<int>(70 * sinf(a)));
  }
  if (dizzy_until_ && millis() >= dizzy_until_) {
    dizzy_until_ = 0;
    refresh();
  }
  // Blink when idle: shut for a moment, then the idle eyes again (happy on a pomodoro break).
  if (mood_ == app::Mood::Idle && millis() - blink_at_ > (blinking_ ? 140u : 3500u)) {
    blink_at_ = millis();
    blinking_ = !blinking_;
    draw_eyes(blinking_ ? sprite::kEyesShut : face_phase_ == "break" ? sprite::kEyesHappy : sprite::kEyesOpen);
  } else if (mood_ != app::Mood::Idle) {
    blinking_ = false;
  }
  if (bubble_until_ && millis() > bubble_until_) hide_bubble();
}

void show(View v);

void on_face_hold(lv_event_t*) {
  if (dictate_start_ && !listening_) dictate_start_();
}

void on_face_release(lv_event_t*) {
  if (listening_ && dictate_stop_) dictate_stop_("");
}

void on_face_tap(lv_event_t*) {
  if (settling()) return;
  if (send_) send_(app::touch());
  show(kMenu);
}

// ---- shared widgets ----

lv_obj_t* make_view(lv_obj_t* scr) {
  lv_obj_t* v = lv_obj_create(scr);
  lv_obj_remove_style_all(v);
  lv_obj_set_size(v, 320, 218);
  lv_obj_remove_flag(v, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_flag(v, LV_OBJ_FLAG_HIDDEN);
  return v;
}

lv_obj_t* button(lv_obj_t* parent, const char* label, lv_color_t color, lv_event_cb_t cb, void* data = nullptr) {
  lv_obj_t* b = lv_button_create(parent);
  lv_obj_set_style_bg_color(b, color, 0);
  lv_obj_set_style_radius(b, 6, 0);
  lv_obj_set_style_shadow_width(b, 0, 0);
  lv_obj_set_style_pad_hor(b, 8, 0);
  lv_obj_set_style_pad_ver(b, 7, 0);
  lv_obj_t* l = text(b, &buddy_font_11, kFg);
  lv_label_set_text(l, label);
  lv_obj_center(l);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, data);
  // On the real 2" screen a finger is ~40 px wide: take touches a little
  // around each button too.
  lv_obj_set_ext_click_area(b, 6);
  return b;
}

void set_button_text(lv_obj_t* b, const char* t) { lv_label_set_text(lv_obj_get_child(b, 0), t); }

lv_obj_t* text_box(lv_obj_t* parent, int x, int y, int w, int h) {
  lv_obj_t* box = lv_obj_create(parent);
  lv_obj_remove_style_all(box);
  lv_obj_set_pos(box, x, y);
  lv_obj_set_size(box, w, h);
  lv_obj_set_style_bg_color(box, kBox, 0);
  lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(box, 4, 0);
  lv_obj_set_style_pad_all(box, 6, 0);
  lv_obj_set_flex_flow(box, LV_FLEX_FLOW_COLUMN);
  return box;
}

void go(lv_event_t* e) {
  if (settling()) return;
  show(static_cast<View>(reinterpret_cast<uintptr_t>(lv_event_get_user_data(e))));
}
void* to(View v) { return reinterpret_cast<void*>(static_cast<uintptr_t>(v)); }

lv_color_t status_color(app::Status s) {
  switch (s) {
    case app::Status::Working: return kInfo;
    case app::Status::Waiting: return kWarn;
    case app::Status::Done: return kOk;
    case app::Status::Offline: return lv_color_hex(0x333333);
    default: return lv_color_hex(0x777777);
  }
}

const char* status_name(app::Status s) {
  switch (s) {
    case app::Status::Working: return "trabalhando";
    case app::Status::Waiting: return "esperando";
    case app::Status::Done: return "terminou";
    case app::Status::Offline: return "offline";
    default: return "tranquila";
  }
}

// ---- list of sessions ----

void on_row(lv_event_t* e) {
  if (settling()) return;
  size_t i = reinterpret_cast<uintptr_t>(lv_event_get_user_data(e));
  if (i < state_.sessions.size()) {
    current_session_ = state_.sessions[i].id;
    show(kSession);
  }
}

void build_list(lv_obj_t* v) {
  lv_obj_t* back = button(v, "◀ Menu", kBtn, go, to(kMenu));
  lv_obj_set_size(back, 80, 30);
  lv_obj_set_pos(back, 4, 3);
  list_count_ = text(v, &buddy_font_11, lv_color_hex(0x7d776f));
  lv_obj_set_pos(list_count_, 94, 12);
  lv_obj_set_width(list_count_, 220);
  lv_label_set_long_mode(list_count_, LV_LABEL_LONG_DOT);
  list_rows_ = lv_obj_create(v);
  lv_obj_remove_style_all(list_rows_);
  lv_obj_set_pos(list_rows_, 4, 36);
  lv_obj_set_size(list_rows_, 312, 180);
  lv_obj_set_flex_flow(list_rows_, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(list_rows_, 4, 0);
  lv_obj_add_flag(list_rows_, LV_OBJ_FLAG_SCROLLABLE);
}

void fill_list() {
  const unsigned n = state_.sessions.size();
  std::string head = std::to_string(n) + (n == 1 ? " sessão" : " sessões");
  // GitHub: PRs awaiting your review, and your PRs with failing CI (short: a 2" screen).
  if (state_.github_reviews) head += " · " + std::to_string(state_.github_reviews) + (state_.github_reviews == 1 ? " PR" : " PRs");
  if (state_.github_failing) head += " · " + std::to_string(state_.github_failing) + " CI ✕";
  lv_label_set_text(list_count_, head.c_str());
  lv_obj_clean(list_rows_);
  if (state_.sessions.empty()) {
    lv_obj_t* l = text(list_rows_, &buddy_font_11, lv_color_hex(0x777777));
    lv_label_set_text(l, "nenhuma sessão ainda");
    return;
  }
  for (size_t i = 0; i < state_.sessions.size(); i++) {
    const auto& s = state_.sessions[i];
    lv_obj_t* row = lv_obj_create(list_rows_);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), 26);
    lv_obj_set_style_bg_color(row, kBox, 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(row, 4, 0);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(row, on_row, LV_EVENT_CLICKED, reinterpret_cast<void*>(i));
    lv_obj_t* dot = box(row, 8, 9, 8, 8, status_color(s.status));
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_t* name = text(row, &buddy_font_11, kFg);
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    lv_obj_set_width(name, 220);
    lv_label_set_text(name, s.name.c_str());
    lv_obj_set_pos(name, 24, 6);
    lv_obj_t* host = text(row, &buddy_font_11, lv_color_hex(0x7d776f));
    lv_label_set_text(host, s.host.c_str());
    lv_obj_align(host, LV_ALIGN_RIGHT_MID, -8, 0);
  }
}

// ---- one session ----

void on_dictate_btn(lv_event_t* e) {
  const lv_event_code_t code = lv_event_get_code(e);
  if (code == LV_EVENT_PRESSED && dictate_start_ && !listening_) dictate_start_();
  if (code == LV_EVENT_RELEASED && listening_ && dictate_stop_) dictate_stop_(current_session_);
}

void build_session(lv_obj_t* v) {
  sess_title_ = text(v, &buddy_font_13, kFg);
  lv_label_set_long_mode(sess_title_, LV_LABEL_LONG_DOT);
  lv_obj_set_width(sess_title_, 304);
  lv_obj_set_pos(sess_title_, 8, 8);
  lv_obj_t* box = text_box(v, 8, 30, 304, 140);
  lv_obj_add_flag(box, LV_OBJ_FLAG_SCROLLABLE);
  sess_msg_ = text(box, &buddy_font_11, kFg);
  lv_obj_set_width(sess_msg_, 290);
  lv_label_set_long_mode(sess_msg_, LV_LABEL_LONG_WRAP);
  button(v, "◀ Voltar", kBtn, go, to(kList));
  lv_obj_set_pos(lv_obj_get_child(v, -1), 8, 178);
  dictate_btn_ = button(v, "Segure para ditar", kBtnInfo, on_dictate_btn);
  lv_obj_add_event_cb(dictate_btn_, on_dictate_btn, LV_EVENT_PRESSED, nullptr);
  lv_obj_add_event_cb(dictate_btn_, on_dictate_btn, LV_EVENT_RELEASED, nullptr);
  lv_obj_align(dictate_btn_, LV_ALIGN_TOP_RIGHT, -8, 178);
}

void fill_session() {
  for (const auto& s : state_.sessions) {
    if (s.id != current_session_) continue;
    lv_label_set_text_fmt(sess_title_, "%s · %s", s.name.c_str(), status_name(s.status));
    lv_label_set_text(sess_msg_, s.last_message.empty() ? "(sem resposta ainda)" : s.last_message.c_str());
    // Dictation needs a mic on the board and the session in tmux (the hub types into its pane).
    const bool can = dictate_start_ && s.has_tmux;
    set_button_text(dictate_btn_, !dictate_start_ ? "Ditar: sem microfone" : s.has_tmux ? "Segure para ditar" : "Ditar: sem tmux");
    if (can) lv_obj_remove_state(dictate_btn_, LV_STATE_DISABLED); else lv_obj_add_state(dictate_btn_, LV_STATE_DISABLED);
    return;
  }
  show(kList);  // the session went away
}

// ---- approval ----

void on_decide(lv_event_t* e) {
  if (settling()) return;
  const bool allow = lv_event_get_user_data(e) != nullptr;
  if (send_ && !approval_id_.empty()) send_(app::decision(approval_id_, allow));
}

void build_approve(lv_obj_t* v) {
  appr_timer_ = box(v, 0, 0, 320, 3, kWarn);
  appr_title_ = text(v, &buddy_font_13, kFg);
  lv_label_set_long_mode(appr_title_, LV_LABEL_LONG_DOT);
  lv_obj_set_width(appr_title_, 304);
  lv_obj_set_pos(appr_title_, 8, 10);
  lv_obj_t* box = text_box(v, 8, 32, 304, 138);
  appr_danger_ = text(box, &buddy_font_11, kBad);
  lv_label_set_text(appr_danger_, "comando perigoso: só por toque");
  appr_body_ = text(box, &buddy_font_11, kFg);
  lv_obj_set_width(appr_body_, 290);
  lv_label_set_long_mode(appr_body_, LV_LABEL_LONG_WRAP);
  lv_obj_t* deny = button(v, "✕ Negar", kBtnBad, on_decide, nullptr);
  lv_obj_set_size(deny, 148, 36);
  lv_obj_set_pos(deny, 8, 176);
  lv_obj_t* allow = button(v, "✓ Aprovar", kBtnOk, on_decide, reinterpret_cast<void*>(1));
  lv_obj_set_size(allow, 148, 36);
  lv_obj_set_pos(allow, 164, 176);
}

void fill_approve() {
  const app::Approval& a = state_.pending.front();
  if (a.id != approval_id_) {
    approval_id_ = a.id;
    approval_seen_ = millis();
    approval_secs_ = a.expires_in > 0 ? a.expires_in : 20;
  }
  lv_label_set_text_fmt(appr_title_, "%s quer usar %s", a.session_name.c_str(), a.tool_name.c_str());
  if (a.dangerous) lv_obj_remove_flag(appr_danger_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(appr_danger_, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(appr_body_, a.summary.empty() ? "(sem detalhes)" : a.summary.c_str());
}

void tick_approve_timer() {
  if (view_ != kApprove || approval_id_.empty()) return;
  float left = approval_secs_ - (millis() - approval_seen_) / 1000.0f;
  if (left < 0) left = 0;
  lv_obj_set_width(appr_timer_, static_cast<int32_t>(320 * left / 20.0f > 320 ? 320 : 320 * left / 20.0f));
}

// ---- settings, in pages by kind (‹ › and "1/5") ----

const int kIntervals[] = {15, 30, 45, 60, 120};
const char* const kLevels[] = {"off", "low", "mid", "high"};   // volume
const char* const kBright[] = {"auto", "low", "mid", "high"};  // brightness
const char* const kPomoLengths[] = {"15+5", "25+5", "50+10"};

template <size_t N>
std::string next_of(const char* const (&options)[N], const std::string& now) {
  size_t i = 0;
  while (i < N && now != options[i]) i++;
  return options[(i + 1) % N];
}

void on_pref(lv_event_t* e) {
  if (settling()) return;
  app::Settings s = state_.settings;
  switch (reinterpret_cast<uintptr_t>(lv_event_get_user_data(e))) {
    case 0: s.jokes = !s.jokes; break;
    case 1: s.joke_voice = !s.joke_voice; break;
    case 2: {
      size_t i = 0;
      while (i < 5 && kIntervals[i] != s.joke_interval_min) i++;
      s.joke_interval_min = kIntervals[(i + 1) % 5];
      break;
    }
    case 3: s.weather = !s.weather; break;
    case 4: s.eyes_skin = !s.eyes_skin; break;
    case 5: s.show_battery = !s.show_battery; break;
    case 6: {
      const int options[] = {0, 10, 15, 30, 60};
      size_t i = 0;
      while (i < 5 && options[i] != s.long_task_min) i++;
      s.long_task_min = options[(i + 1) % 5];
      break;
    }
    case 7: s.pomodoro = next_of(kPomoLengths, s.pomodoro); break;
    case 8:
      if (setup_handler_) setup_handler_();
      return;
    case 9: s.volume = next_of(kLevels, s.volume); break;
    case 10: s.brightness = next_of(kBright, s.brightness); break;
    case 11: s.mic = !s.mic; break;
    case 12: {
      const int options[] = {0, 1, 5, 15};
      size_t i = 0;
      while (i < 4 && options[i] != s.flat_sleep_min) i++;
      s.flat_sleep_min = options[(i + 1) % 4];
      break;
    }
    case 13: s.face_down_sleep = !s.face_down_sleep; break;
  }
  if (send_) send_(app::settings(s));
}

struct Page {
  const char* title;
  int prefs[4];  // -1 ends the list
  const char* note;
};
constexpr Page kPages[] = {
    {"Piadas", {0, 1, 2, -1}, nullptr},
    {"Som e tela", {9, 10, 4, 11}, nullptr},
    {"Pomodoro", {7, -1}, "A duração vale a partir do próximo pomodoro."},
    {"Avisos e clima", {3, 6, 5, -1}, nullptr},
    {"Sono", {12, 13, -1}, "Dorme sem toque deitado, ou virado para baixo. Acorda ao tocar ou levantar."},
    {"WiFi e hub", {8, -1}, "Configurar abre a rede Buddy-setup para trocar o WiFi ou o hub."},
    {"Sistema", {-1}, nullptr},  // live numbers, see set_stats
};
constexpr int kSystemPage = 6;
constexpr int kPageCount = sizeof(kPages) / sizeof(*kPages);
lv_obj_t* pages_[kPageCount];
lv_obj_t *page_title_, *page_count_;
lv_obj_t* stats_values_ = nullptr;  // the right column of the Sistema page
int page_ = 0;

void show_page(int i) {
  page_ = (i + kPageCount) % kPageCount;
  for (int p = 0; p < kPageCount; p++)
    if (p == page_) lv_obj_remove_flag(pages_[p], LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(pages_[p], LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text_fmt(page_title_, "⚙ %s", kPages[page_].title);
  lv_label_set_text_fmt(page_count_, "%d/%d", page_ + 1, kPageCount);
}

void on_page(lv_event_t* e) {
  if (settling()) return;
  show_page(page_ + (lv_event_get_user_data(e) ? 1 : -1));
}

void build_settings(lv_obj_t* v) {
  page_title_ = text(v, &buddy_font_13, kFg);
  lv_obj_set_pos(page_title_, 8, 8);
  const char* names[kPrefs] = {"Piadas de vez em quando", "Falar as piadas", "Intervalo entre piadas",
                               "Temperatura lá fora", "Visual", "Indicador de bateria",
                               "Aviso de tarefa longa", "Foco + pausa", "WiFi e hub", "Volume", "Brilho",
                               "Microfone (botão)", "Deitado (tela p/ cima)", "Virado para baixo"};
  for (int p = 0; p < kPageCount; p++) {
    pages_[p] = lv_obj_create(v);
    lv_obj_remove_style_all(pages_[p]);
    lv_obj_set_pos(pages_[p], 8, 30);
    lv_obj_set_size(pages_[p], 304, 144);
    lv_obj_set_flex_flow(pages_[p], LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(pages_[p], 4, 0);
    lv_obj_remove_flag(pages_[p], LV_OBJ_FLAG_SCROLLABLE);
    for (int i : kPages[p].prefs) {
      if (i < 0) break;
      lv_obj_t* row = text_box(pages_[p], 0, 0, 304, 32);
      lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
      lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
      lv_obj_set_style_pad_ver(row, 2, 0);
      lv_obj_t* l = text(row, &buddy_font_11, kFg);
      lv_label_set_text(l, names[i]);
      if (i == 3) weather_label_ = l;
      pref_btn_[i] = button(row, "", kBtn, on_pref, reinterpret_cast<void*>(static_cast<uintptr_t>(i)));
      lv_obj_set_size(pref_btn_[i], 104, 26);
      lv_obj_set_style_pad_ver(pref_btn_[i], 0, 0);
    }
    if (p == kSystemPage) {
      lv_obj_t* panel = text_box(pages_[p], 0, 0, 304, 140);
      lv_obj_set_flex_flow(panel, LV_FLEX_FLOW_ROW);
      lv_obj_t* names = text(panel, &buddy_font_11, kMuted);
      lv_label_set_text(names, "CPU\nMemória\nPSRAM\nTela (LVGL)\nFirmware\nWiFi\nIP\nLigado há");
      lv_obj_set_width(names, 110);
      stats_values_ = text(panel, &buddy_font_11, kFg);
      lv_label_set_text(stats_values_, "…");
      lv_obj_set_style_text_line_space(names, 3, 0);
      lv_obj_set_style_text_line_space(stats_values_, 3, 0);
    }
    if (kPages[p].note) {
      lv_obj_t* note = text(pages_[p], &buddy_font_11, lv_color_hex(0x7d776f));
      lv_obj_set_width(note, 300);
      lv_label_set_long_mode(note, LV_LABEL_LONG_WRAP);
      lv_label_set_text(note, kPages[p].note);
    }
  }
  lv_obj_t* back = button(v, "◀ Menu", kBtn, go, to(kMenu));
  lv_obj_set_size(back, 80, 34);
  lv_obj_set_pos(back, 8, 180);
  lv_obj_t* prev = button(v, "‹", kBtn, on_page, nullptr);
  lv_obj_set_size(prev, 56, 34);
  lv_obj_set_pos(prev, 160, 180);
  lv_obj_set_style_text_font(lv_obj_get_child(prev, 0), &buddy_font_13, 0);
  page_count_ = text(v, &buddy_font_13, kMuted);
  lv_obj_set_width(page_count_, 40);
  lv_obj_set_style_text_align(page_count_, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_pos(page_count_, 216, 189);
  lv_obj_t* next = button(v, "›", kBtn, on_page, reinterpret_cast<void*>(1));
  lv_obj_set_size(next, 56, 34);
  lv_obj_set_pos(next, 256, 180);
  lv_obj_set_style_text_font(lv_obj_get_child(next, 0), &buddy_font_13, 0);
  show_page(0);
}

void fill_settings() {
  const app::Settings& st = state_.settings;
  bool on[kPrefs] = {st.jokes, st.joke_voice, false, st.weather, false, st.show_battery};
  on[11] = st.mic;
  on[13] = st.face_down_sleep;
  for (int i : {0, 1, 3, 5, 11, 13}) {
    set_button_text(pref_btn_[i], on[i] ? "Ligado" : "Desligado");
    lv_obj_set_style_bg_color(pref_btn_[i], on[i] ? kBtnOk : kBtn, 0);
  }
  char buf[24];
  snprintf(buf, sizeof(buf), "%d min", st.joke_interval_min);
  set_button_text(pref_btn_[2], buf);
  set_button_text(pref_btn_[4], st.eyes_skin ? "Só os olhos" : "Clássico");
  if (st.long_task_min) {
    snprintf(buf, sizeof(buf), "%d min", st.long_task_min);
    set_button_text(pref_btn_[6], buf);
  } else {
    set_button_text(pref_btn_[6], "Desligado");
  }
  std::string pomo = st.pomodoro;
  const size_t plus = pomo.find('+');
  if (plus != std::string::npos) pomo = pomo.substr(0, plus) + " + " + pomo.substr(plus + 1) + " min";
  set_button_text(pref_btn_[7], pomo.c_str());
  set_button_text(pref_btn_[8], "Configurar");
  if (st.flat_sleep_min) {
    snprintf(buf, sizeof(buf), "%d min", st.flat_sleep_min);
    set_button_text(pref_btn_[12], buf);
  } else {
    set_button_text(pref_btn_[12], "Desligado");
  }
  set_button_text(pref_btn_[9], st.volume == "off" ? "Mudo" : st.volume == "low" ? "Baixo" : st.volume == "high" ? "Alto" : "Médio");
  set_button_text(pref_btn_[10], st.brightness == "low" ? "Baixo" : st.brightness == "mid" ? "Médio"
                                 : st.brightness == "high" ? "Alto" : "Automático");
  // The city is changed from the web panel (no keyboard on a 2" screen).
  lv_label_set_text_fmt(weather_label_, "Temperatura em %s", st.city.c_str());
}

// ---- menu: a tap on the face opens big buttons ----

lv_obj_t *menu_sessions_, *menu_pomo_, *menu_pomo_sub_;

int pomo_left() {  // seconds left in the current phase
  const int left = pomo_secs_ - static_cast<int>((millis() - pomo_at_) / 1000);
  return left < 0 ? 0 : left;
}

lv_obj_t* tile(lv_obj_t* parent, int x, int y, const char* title, lv_color_t color, lv_event_cb_t cb, void* data,
               lv_obj_t** sub) {
  lv_obj_t* b = lv_button_create(parent);
  lv_obj_set_pos(b, x, y);
  lv_obj_set_size(b, 148, 84);
  lv_obj_set_style_bg_color(b, color, 0);
  lv_obj_set_style_radius(b, 8, 0);
  lv_obj_set_style_shadow_width(b, 0, 0);
  lv_obj_set_flex_flow(b, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(b, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_row(b, 4, 0);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, data);
  lv_label_set_text(text(b, &buddy_font_13, kFg), title);
  *sub = text(b, &buddy_font_11, lv_color_hex(0xc9c4bc));
  lv_label_set_text(*sub, "");
  return b;
}

void on_menu_joke(lv_event_t*) {
  if (settling()) return;
  if (send_) send_(app::joke_now());
  show(kFace);
}

void build_menu(lv_obj_t* v) {
  lv_obj_t* back = button(v, "◀ Buddy", kBtn, go, to(kFace));
  lv_obj_set_size(back, 88, 30);
  lv_obj_set_pos(back, 8, 4);
  lv_obj_t* sub;
  tile(v, 8, 42, "Sessões", kBtn, go, to(kList), &menu_sessions_);
  tile(v, 164, 42, "⚙ Ajustes", kBtn, go, to(kSettings), &sub);
  lv_label_set_text(sub, "piadas, voz, tela");
  tile(v, 8, 132, "✦ Piada", kBtnInfo, on_menu_joke, nullptr, &sub);
  lv_label_set_text(sub, "conta uma agora");
  menu_pomo_ = tile(v, 164, 132, "Pomodoro", kBtn, go, to(kPomo), &menu_pomo_sub_);
}

void fill_menu() {
  int open = 0, working = 0, waiting = 0;
  for (const auto& s : state_.sessions) {
    if (s.status == app::Status::Offline) continue;
    open++;
    working += s.status == app::Status::Working;
    waiting += s.status == app::Status::Waiting;
  }
  if (!open) lv_label_set_text(menu_sessions_, "nenhuma aberta");
  else if (waiting) lv_label_set_text_fmt(menu_sessions_, "%d abertas · %d esperando", open, waiting);
  else lv_label_set_text_fmt(menu_sessions_, "%d abertas · %d trabalhando", open, working);
  const bool running = !state_.pomodoro.phase.empty();
  lv_obj_set_style_bg_color(menu_pomo_, running ? kBtnOk : kBtn, 0);
  if (running) {
    const int left = pomo_left();
    lv_label_set_text_fmt(menu_pomo_sub_, "%s %02d:%02d", state_.pomodoro.phase == "focus" ? "foco" : "pausa",
                          left / 60, left % 60);
  } else {
    lv_label_set_text_fmt(menu_pomo_sub_, "%d min de foco", atoi(state_.settings.pomodoro.c_str()));
  }
}

// ---- pomodoro: the countdown big enough to read from the chair ----

const lv_color_t kFocus = lv_color_hex(0xe8875f), kBreak = lv_color_hex(0x6fc59a);
lv_obj_t *pomo_phase_, *pomo_time_, *pomo_fill_, *pomo_rounds_, *pomo_skip_, *pomo_toggle_;

void on_pomo_toggle(lv_event_t*) {
  if (settling()) return;
  if (send_) send_(app::pomodoro(state_.pomodoro.phase.empty()));
}

void on_pomo_skip(lv_event_t*) {
  if (settling() || state_.pomodoro.phase.empty()) return;
  if (send_) send_(app::pomodoro_skip());
}

void build_pomo(lv_obj_t* v) {
  pomo_phase_ = text(v, &buddy_font_13, kMuted);
  lv_obj_align(pomo_phase_, LV_ALIGN_TOP_MID, 0, 18);
  pomo_time_ = text(v, &buddy_font_48, kFg);
  lv_obj_align(pomo_time_, LV_ALIGN_TOP_MID, 0, 42);
  lv_obj_t* track = box(v, 16, 108, 288, 8, kBar);
  lv_obj_set_style_radius(track, 4, 0);
  pomo_fill_ = box(track, 0, 0, 0, 8, kFocus);
  lv_obj_set_style_radius(pomo_fill_, 4, 0);
  pomo_rounds_ = text(v, &buddy_font_11, lv_color_hex(0x7d776f));
  lv_obj_align(pomo_rounds_, LV_ALIGN_TOP_MID, 0, 126);
  lv_obj_t* back = button(v, "◀ Menu", kBtn, go, to(kMenu));
  lv_obj_set_size(back, 96, 38);
  lv_obj_set_pos(back, 8, 172);
  pomo_skip_ = button(v, "Pular", kBtn, on_pomo_skip);
  lv_obj_set_size(pomo_skip_, 96, 38);
  lv_obj_set_pos(pomo_skip_, 112, 172);
  pomo_toggle_ = button(v, "▶ Iniciar", kBtnOk, on_pomo_toggle);
  lv_obj_set_size(pomo_toggle_, 96, 38);
  lv_obj_set_pos(pomo_toggle_, 216, 172);
}

void fill_pomo() {
  const app::Pomodoro& p = state_.pomodoro;
  const bool running = !p.phase.empty(), focus = p.phase == "focus";
  const int left = running ? pomo_left() : atoi(state_.settings.pomodoro.c_str()) * 60;
  lv_label_set_text_fmt(pomo_time_, "%02d:%02d", left / 60, left % 60);
  lv_label_set_text(pomo_phase_, !running ? "POMODORO" : focus ? "FOCO" : "PAUSA");
  lv_obj_set_style_text_color(pomo_phase_, !running ? kMuted : focus ? kFocus : kBreak, 0);
  lv_obj_set_style_bg_color(pomo_fill_, focus ? kFocus : kBreak, 0);
  lv_obj_set_width(pomo_fill_, running && p.length ? 288 * (p.length - left) / p.length : 0);
  if (!running) {
    const std::string& len = state_.settings.pomodoro;
    const size_t plus = len.find('+');
    lv_label_set_text_fmt(pomo_rounds_, "%s min de foco + %s de pausa", len.substr(0, plus).c_str(),
                          plus == std::string::npos ? "5" : len.substr(plus + 1).c_str());
  } else if (p.rounds) {
    lv_label_set_text_fmt(pomo_rounds_, "%d %s", p.rounds, p.rounds == 1 ? "ciclo completo" : "ciclos completos");
  } else {
    lv_label_set_text(pomo_rounds_, "primeiro ciclo");
  }
  set_button_text(pomo_toggle_, running ? "■ Parar" : "▶ Iniciar");
  lv_obj_set_style_bg_color(pomo_toggle_, running ? kBtnBad : kBtnOk, 0);
  set_button_text(pomo_skip_, running && !focus ? "Pular pausa" : "Pausar já");
  if (running) lv_obj_remove_state(pomo_skip_, LV_STATE_DISABLED); else lv_obj_add_state(pomo_skip_, LV_STATE_DISABLED);
}

// ---- microphone: 🎤 on the face → record (live wave) → read the text → pick where it goes ----

lv_obj_t *rec_time_, *rec_dot_, *rec_hint_;
constexpr int kWaveBars = 40;
lv_obj_t* rec_bars_[kWaveBars];
int rec_levels_[kWaveBars];
uint32_t rec_tick_ = 0;
lv_obj_t *pick_text_, *pick_list_;
std::string pick_transcript_;  // empty until the hub answers
std::string last_target_;      // the session dictated to last: marked in the list
// What each row of the list sends to: "@command", "focus" or a session id. A copy,
// since state_ (and its strings) is replaced by every state message.
std::vector<std::string> pick_targets_;
uint32_t pick_back_at_ = 0;     // after a result: back to the face then

void on_mic(lv_event_t*) {
  if (settling() || !mic_.start) return;
  if (send_) send_(app::touch());
  if (!mic_.start()) {
    on_notice("Não consegui ligar o microfone.");
    return;
  }
  for (int& l : rec_levels_) l = 0;
  lv_label_set_text(rec_time_, "0:00");
  show(kRec);
}

void on_rec_done(lv_event_t* e) {
  if (settling() || !mic_.finish) return;
  const bool send = lv_event_get_user_data(e) != nullptr;
  if (send) {
    pick_transcript_.clear();
    pick_back_at_ = 0;
    show(kPick);
    lv_refr_now(nullptr);  // "transcrevendo…" on screen while finish() blocks
  } else {
    show(kFace);
  }
  mic_.finish(send);
}

void build_rec(lv_obj_t* v) {
  rec_dot_ = box(v, 10, 13, 10, 10, kBad);
  lv_obj_set_style_radius(rec_dot_, LV_RADIUS_CIRCLE, 0);
  lv_obj_t* title = text(v, &buddy_font_13, kFg);
  lv_label_set_text(title, "Gravando");
  lv_obj_set_pos(title, 28, 9);
  rec_time_ = text(v, &buddy_font_13, kMuted);
  lv_obj_align(rec_time_, LV_ALIGN_TOP_RIGHT, -10, 9);
  lv_obj_t* wave = box(v, 8, 34, 304, 100, kBox);
  lv_obj_set_style_radius(wave, 6, 0);
  for (int i = 0; i < kWaveBars; i++) rec_bars_[i] = box(wave, 4 + i * 7 + 1, 48, 5, 4, lv_color_hex(0xe8875f));
  rec_hint_ = text(v, &buddy_font_11, lv_color_hex(0x7d776f));
  lv_label_set_text(rec_hint_, "fale; toque em Enviar quando terminar");
  lv_obj_align(rec_hint_, LV_ALIGN_TOP_MID, 0, 142);
  lv_obj_t* cancel = button(v, "✕ Cancelar", kBtnBad, on_rec_done, nullptr);
  lv_obj_set_size(cancel, 148, 40);
  lv_obj_set_pos(cancel, 8, 170);
  lv_obj_t* send = button(v, "Enviar ▶", kBtnOk, on_rec_done, reinterpret_cast<void*>(1));
  lv_obj_set_size(send, 148, 40);
  lv_obj_set_pos(send, 164, 170);
}

void tick_rec() {
  if (view_ != kRec || !mic_.level || millis() - rec_tick_ < 80) return;
  rec_tick_ = millis();
  for (int i = 0; i < kWaveBars - 1; i++) rec_levels_[i] = rec_levels_[i + 1];
  rec_levels_[kWaveBars - 1] = mic_.level();
  for (int i = 0; i < kWaveBars; i++) {
    const int h = rec_levels_[i] < 5 ? 4 : rec_levels_[i] * 90 / 100;
    lv_obj_set_height(rec_bars_[i], h);
    lv_obj_set_y(rec_bars_[i], 50 - h / 2);
  }
  const int secs = static_cast<int>(mic_.seconds ? mic_.seconds() : 0);
  lv_label_set_text_fmt(rec_time_, "%d:%02d", secs / 60, secs % 60);
  lv_obj_set_style_opa(rec_dot_, (millis() / 500) % 2 ? LV_OPA_COVER : LV_OPA_30, 0);
  if (secs >= 60) {  // the limit: send what there is
    lv_obj_send_event(lv_obj_get_child(views_[kRec], -1), LV_EVENT_CLICKED, nullptr);
  }
}

void on_pick(lv_event_t* e) {
  if (settling() || pick_transcript_.empty() || !send_) return;
  const size_t i = reinterpret_cast<uintptr_t>(lv_event_get_user_data(e));
  if (i >= pick_targets_.size()) return;
  if (pick_targets_[i] == "@command") {
    lv_label_set_text(pick_text_, "enviando comando…");
    send_(app::command(pick_transcript_));
    return;
  }
  last_target_ = pick_targets_[i];
  lv_label_set_text(pick_text_, last_target_ == "focus" ? "digitando na janela em foco…" : "enviando…");
  send_(app::dictate(last_target_, pick_transcript_));
}

lv_obj_t* pick_row(const char* label, const char* sub, lv_color_t color, const std::string& target, bool enabled,
                   bool last) {
  pick_targets_.push_back(target);
  lv_obj_t* row = lv_button_create(pick_list_);
  lv_obj_set_size(row, LV_PCT(100), 30);
  lv_obj_set_style_bg_color(row, color, 0);
  lv_obj_set_style_radius(row, 6, 0);
  lv_obj_set_style_shadow_width(row, 0, 0);
  lv_obj_set_style_pad_hor(row, 8, 0);
  if (last) {
    lv_obj_set_style_border_width(row, 2, 0);
    lv_obj_set_style_border_color(row, kInfo, 0);
  }
  lv_obj_t* l = text(row, &buddy_font_11, kFg);
  lv_label_set_text(l, label);
  lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
  lv_obj_set_width(l, 190);
  lv_obj_align(l, LV_ALIGN_LEFT_MID, 0, 0);
  lv_obj_t* r = text(row, &buddy_font_11, lv_color_hex(0xc9c4bc));
  lv_label_set_text(r, sub);
  lv_obj_align(r, LV_ALIGN_RIGHT_MID, 0, 0);
  lv_obj_add_event_cb(row, on_pick, LV_EVENT_CLICKED, reinterpret_cast<void*>(pick_targets_.size() - 1));
  if (!enabled) lv_obj_add_state(row, LV_STATE_DISABLED);
  return row;
}

void fill_pick() {
  lv_label_set_text(pick_text_, pick_transcript_.empty() ? "transcrevendo…" : pick_transcript_.c_str());
  lv_obj_clean(pick_list_);
  pick_targets_.clear();
  const bool ready = !pick_transcript_.empty();
  pick_row("Comando de voz", "pomodoro, boa noite…", kBtnInfo, "@command", ready, false);
  // Sessions outside tmux: type into the focused window on the notebook, like F9.
  pick_row("Janela em foco", "no notebook, como o F9", kBtn, "focus", ready, last_target_ == "focus");
  for (int pass = 0; pass < 2; pass++)  // the last session used first
    for (const auto& x : state_.sessions) {
      if (x.status == app::Status::Offline || (x.id == last_target_) != (pass == 0)) continue;
      pick_row(x.name.c_str(), x.has_tmux ? status_name(x.status) : "sem tmux", kBtn, x.id,
               ready && x.has_tmux, x.id == last_target_);
    }
}

void build_pick(lv_obj_t* v) {
  lv_obj_t* title = text(v, &buddy_font_13, kFg);
  lv_label_set_text(title, "Para onde?");
  lv_obj_set_pos(title, 8, 6);
  lv_obj_t* tb = text_box(v, 8, 26, 304, 44);
  lv_obj_add_flag(tb, LV_OBJ_FLAG_SCROLLABLE);
  pick_text_ = text(tb, &buddy_font_11, kFg);
  lv_obj_set_width(pick_text_, 290);
  lv_label_set_long_mode(pick_text_, LV_LABEL_LONG_WRAP);
  pick_list_ = lv_obj_create(v);
  lv_obj_remove_style_all(pick_list_);
  lv_obj_set_pos(pick_list_, 8, 74);
  lv_obj_set_size(pick_list_, 304, 96);
  lv_obj_set_flex_flow(pick_list_, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(pick_list_, 4, 0);
  lv_obj_add_flag(pick_list_, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_t* cancel = button(v, "✕ Cancelar", kBtn, go, to(kFace));
  lv_obj_set_size(cancel, 304, 36);
  lv_obj_set_pos(cancel, 8, 176);
}

// ---- power button: a window with Dormir / Reiniciar / Desligar ----

void close_power_menu() {
  if (!power_menu_) return;
  lv_obj_delete(power_menu_);
  power_menu_ = nullptr;
}

void on_power_choice(lv_event_t* e) {
  const uintptr_t choice = reinterpret_cast<uintptr_t>(lv_event_get_user_data(e));
  close_power_menu();
  if (choice == 1 && power_.sleep) power_.sleep();
  if (choice == 2 && power_.restart) {
    show_message("Reiniciando…", "volto em alguns segundos");
    power_.restart();
  }
  if (choice == 3 && power_.off) {
    show_message("Desligando…", "aperte o power para ligar de novo");
    delay(800);
    power_.off();
  }
}

void open_power_menu() {
  power_menu_ = box(lv_layer_top(), 0, 0, 320, 240, kBg);
  lv_obj_set_style_bg_opa(power_menu_, LV_OPA_80, 0);
  lv_obj_add_flag(power_menu_, LV_OBJ_FLAG_CLICKABLE);  // swallow taps around the panel
  lv_obj_t* panel = box(power_menu_, 30, 14, 260, 212, kBox);
  lv_obj_set_style_radius(panel, 10, 0);
  lv_obj_t* title = text(panel, &buddy_font_13, kFg);
  lv_label_set_text(title, "Energia");
  lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 8);
  const struct { const char* label; lv_color_t color; } items[] = {
      {"☾ Dormir", kBtnInfo}, {"Reiniciar", kBtn}, {"Desligar", kBtnBad}, {"Cancelar", kBtn}};
  for (int i = 0; i < 4; i++) {
    lv_obj_t* b = button(panel, items[i].label, items[i].color, on_power_choice,
                         reinterpret_cast<void*>(static_cast<uintptr_t>(i == 3 ? 0 : i + 1)));
    lv_obj_set_size(b, 236, 38);
    lv_obj_set_pos(b, 12, 32 + i * 44);
  }
  power_menu_at_ = millis();
  lv_display_trigger_activity(nullptr);
}

// ---- navigation ----

void show(View v) {
  if (v != view_) view_changed_at_ = millis();
  view_ = v;
  for (int i = 0; i < kViews; i++)
    if (i == v) lv_obj_remove_flag(views_[i], LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(views_[i], LV_OBJ_FLAG_HIDDEN);
  if (v == kList) fill_list();
  if (v == kSession) fill_session();
  if (v == kSettings) fill_settings();
  if (v == kMenu) fill_menu();
  if (v == kPomo) fill_pomo();
  if (v == kPick) fill_pick();
  if (v == kApprove && !state_.pending.empty()) fill_approve();
  lv_display_trigger_activity(nullptr);  // restarts the idle-return countdown
}

void route() {
  // Like the simulator: a pending approval takes over the screen; when it is
  // gone, back to the face. Lists left alone go back to the face after 30 s.
  if (view_ == kRec || view_ == kPick) return;  // recording or choosing: an approval waits (as in the simulator)
  if (!state_.pending.empty() && view_ != kApprove) show(kApprove);
  else if (state_.pending.empty() && view_ == kApprove) { approval_id_.clear(); show(kFace); }
  else if (view_ == kApprove) fill_approve();
  else if (view_ == kList) fill_list();
  else if (view_ == kSession) fill_session();
  else if (view_ == kSettings) fill_settings();
  else if (view_ == kMenu) fill_menu();
  else if (view_ == kPomo) fill_pomo();
}

}  // namespace

void begin(Sender send) {
  send_ = send;
  lv_obj_t* scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, kBg, 0);
  lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  for (auto& v : views_) v = make_view(scr);
  face_ = views_[kFace];
  lv_obj_add_flag(face_, LV_OBJ_FLAG_CLICKABLE);
  // A tap opens the list; holding it is push-to-talk for voice commands.
  lv_obj_add_event_cb(face_, on_face_tap, LV_EVENT_SHORT_CLICKED, nullptr);
  lv_obj_add_event_cb(face_, on_face_hold, LV_EVENT_LONG_PRESSED, nullptr);
  lv_obj_add_event_cb(face_, on_face_release, LV_EVENT_RELEASED, nullptr);
  build_list(views_[kList]);
  build_session(views_[kSession]);
  build_approve(views_[kApprove]);
  build_settings(views_[kSettings]);
  build_menu(views_[kMenu]);
  build_pomo(views_[kPomo]);
  build_rec(views_[kRec]);
  build_pick(views_[kPick]);
  build_sprite();
  bigeyes_ = lv_obj_create(face_);
  lv_obj_remove_style_all(bigeyes_);
  lv_obj_set_pos(bigeyes_, kBigX, kBigY);
  lv_obj_set_size(bigeyes_, 12 * kBigUnit, 5 * kBigUnit);
  lv_obj_remove_flag(bigeyes_, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_flag(bigeyes_, LV_OBJ_FLAG_HIDDEN);

  // Pomodoro look (see show_pomodoro_look); sprite units, drawn past the body's box.
  const struct { int8_t x, y, w, h; uint32_t color; } phones[] = {
      {4, -2, 10, 1, 0x8a8f99}, {3, -1, 1, 1, 0x8a8f99}, {14, -1, 1, 1, 0x8a8f99},  // band
      {1, 0, 2, 4, 0x4a4f59},   {15, 0, 2, 4, 0x4a4f59},                             // cups
      {2, 1, 1, 2, 0xe8875f},   {15, 1, 1, 2, 0xe8875f}};                            // pads
  for (auto& r : phones)
    phones_.push_back(box(sprite_, kPad + r.x * kUnit, kPad + r.y * kUnit, r.w * kUnit, r.h * kUnit, lv_color_hex(r.color)));
  mug_.push_back(box(sprite_, kPad + 18 * kUnit, kPad + 5 * kUnit, 3 * kUnit, 3 * kUnit, lv_color_hex(0xe9e4dc)));
  mug_.push_back(box(sprite_, kPad + 21 * kUnit, kPad + 6 * kUnit, kUnit, kUnit, lv_color_hex(0xe9e4dc)));
  mug_.push_back(box(sprite_, kPad + 18 * kUnit, kPad + 5 * kUnit, 3 * kUnit, kUnit, lv_color_hex(0x7a4a2e)));
  fx_steam_[0] = box(sprite_, kPad + 186, kPad + 30, 4, 10, lv_color_hex(0x8a857f));
  fx_steam_[1] = box(sprite_, kPad + 196, kPad + 26, 4, 10, lv_color_hex(0x8a857f));
  fx_notes_[0] = text(face_, &buddy_font_13, lv_color_hex(0xe8875f));
  lv_label_set_text(fx_notes_[0], "♪");
  lv_obj_set_pos(fx_notes_[0], kSpriteX + 168, kSpriteY - 18);
  fx_notes_[1] = text(face_, &buddy_font_13, lv_color_hex(0xe8875f));
  lv_label_set_text(fx_notes_[1], "♫");
  lv_obj_set_pos(fx_notes_[1], kSpriteX - 22, kSpriteY - 12);
  for (auto*& star : fx_stars_) {
    star = text(face_, &buddy_font_13, kWarn);
    lv_label_set_text(star, "✦");
    lv_obj_add_flag(star, LV_OBJ_FLAG_HIDDEN);
  }
  pomo_track_ = box(face_, kSpriteX + 10, kSpriteY + 108, 160, 5, kBar);
  pomo_mini_ = box(pomo_track_, 0, 0, 0, 5, lv_color_hex(0xe8875f));
  for (auto* o : phones_) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  for (auto* o : mug_) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  for (auto* o : {fx_steam_[0], fx_steam_[1], fx_notes_[0], fx_notes_[1], pomo_track_}) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);

  label_ = text(face_, &buddy_font_11, kMuted);
  lv_obj_set_width(label_, 304);
  lv_obj_set_style_text_align(label_, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_long_mode(label_, LV_LABEL_LONG_DOT);
  lv_obj_align(label_, LV_ALIGN_TOP_MID, 0, 176);

  weather_ = text(face_, &buddy_font_11, kMuted);
  lv_obj_set_pos(weather_, 8, 6);
  // 🎤 (Ajustes → Som e tela → Microfone): no emoji in the font, so a drawn mic.
  mic_btn_ = lv_button_create(face_);
  lv_obj_set_size(mic_btn_, 44, 38);
  lv_obj_set_pos(mic_btn_, 6, 4);
  lv_obj_set_style_bg_color(mic_btn_, kBtnInfo, 0);
  lv_obj_set_style_radius(mic_btn_, 10, 0);
  lv_obj_set_style_shadow_width(mic_btn_, 0, 0);
  lv_obj_set_ext_click_area(mic_btn_, 6);
  lv_obj_add_event_cb(mic_btn_, on_mic, LV_EVENT_CLICKED, nullptr);
  lv_obj_t* capsule = box(mic_btn_, 0, 0, 10, 16, kFg);
  lv_obj_set_style_radius(capsule, 5, 0);
  lv_obj_align(capsule, LV_ALIGN_TOP_MID, 0, -4);
  lv_obj_t* cradle = lv_obj_create(mic_btn_);
  lv_obj_remove_style_all(cradle);
  lv_obj_set_size(cradle, 18, 13);
  lv_obj_set_style_border_width(cradle, 2, 0);
  lv_obj_set_style_border_color(cradle, kFg, 0);
  lv_obj_set_style_border_side(cradle, static_cast<lv_border_side_t>(LV_BORDER_SIDE_BOTTOM | LV_BORDER_SIDE_LEFT | LV_BORDER_SIDE_RIGHT), 0);
  lv_obj_set_style_radius(cradle, 7, 0);
  lv_obj_remove_flag(cradle, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_align(cradle, LV_ALIGN_TOP_MID, 0, 2);
  lv_obj_t* stem = box(mic_btn_, 0, 0, 2, 5, kFg);
  lv_obj_align(stem, LV_ALIGN_BOTTOM_MID, 0, 0);
  lv_obj_add_flag(mic_btn_, LV_OBJ_FLAG_HIDDEN);

  fx_alert_ = text(face_, &buddy_font_13, kWarn);
  lv_label_set_text(fx_alert_, "!");
  lv_obj_set_pos(fx_alert_, kSpriteX + 85, kSpriteY - 34);
  const char* zs[] = {"z", "z", "Z"};
  for (int i = 0; i < 3; i++) {  // like the simulator: (15,-0.5), (16,-1.5), (17,-2.5) in sprite units
    fx_zzz_[i] = text(face_, &buddy_font_13, lv_color_hex(0x8a857f));
    lv_label_set_text(fx_zzz_[i], zs[i]);
    lv_obj_set_pos(fx_zzz_[i], kSpriteX + 150 + i * 10, kSpriteY - 18 - i * 10);
  }
  const int wave_x[] = {-24, -13, 187, 198};  // sound bars: x -2.4, -1.3, 18.7, 19.8 units
  for (int i = 0; i < 4; i++) fx_wave_[i] = box(sprite_, kPad + wave_x[i], kPad + 35, 6, 30, kAccent);
  fx_spark_[0] = text(face_, &buddy_font_13, kOk);
  fx_spark_[1] = text(face_, &buddy_font_13, kOk);
  lv_label_set_text(fx_spark_[0], "✦");
  lv_label_set_text(fx_spark_[1], "✦");
  lv_obj_set_pos(fx_spark_[0], kSpriteX - 4, kSpriteY - 26);
  lv_obj_set_pos(fx_spark_[1], kSpriteX + 168, kSpriteY - 36);

  bubble_ = lv_obj_create(face_);
  lv_obj_remove_style_all(bubble_);
  lv_obj_set_size(bubble_, 170, LV_SIZE_CONTENT);
  lv_obj_set_style_bg_color(bubble_, kFg, 0);
  lv_obj_set_style_bg_opa(bubble_, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(bubble_, 8, 0);
  lv_obj_set_style_pad_all(bubble_, 6, 0);
  lv_obj_align(bubble_, LV_ALIGN_TOP_RIGHT, -8, 8);
  bubble_text_ = text(bubble_, &buddy_font_11, kBar);
  lv_obj_set_width(bubble_text_, 158);
  lv_label_set_long_mode(bubble_text_, LV_LABEL_LONG_WRAP);
  lv_obj_add_flag(bubble_, LV_OBJ_FLAG_HIDDEN);

  lv_obj_t* bar = box(scr, 0, 218, 320, 22, kBar);
  bar_left_ = text(bar, &buddy_font_11, kMuted);
  lv_obj_align(bar_left_, LV_ALIGN_LEFT_MID, 8, 0);
  bar_mid_ = text(bar, &buddy_font_11, kAccent);
  lv_label_set_text(bar_mid_, "");
  lv_obj_set_width(bar_mid_, 130);
  lv_label_set_long_mode(bar_mid_, LV_LABEL_LONG_DOT);
  lv_obj_set_style_text_align(bar_mid_, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(bar_mid_, LV_ALIGN_CENTER, 10, 0);
  // Battery: an outlined cell with a fill proportional to the charge, and the
  // percentage beside it. Hidden on boards without a battery.
  batt_shell_ = lv_obj_create(bar);
  lv_obj_remove_style_all(batt_shell_);
  lv_obj_set_size(batt_shell_, 22, 11);
  lv_obj_set_style_border_width(batt_shell_, 1, 0);
  lv_obj_set_style_border_color(batt_shell_, kMuted, 0);
  lv_obj_set_style_radius(batt_shell_, 2, 0);
  lv_obj_align(batt_shell_, LV_ALIGN_RIGHT_MID, -10, 0);
  lv_obj_t* cap = box(bar, 0, 0, 2, 5, kMuted);
  lv_obj_align_to(cap, batt_shell_, LV_ALIGN_OUT_RIGHT_MID, 0, 0);
  batt_fill_ = box(batt_shell_, 2, 2, 0, 7, kOk);
  batt_bolt_ = text(batt_shell_, &buddy_font_11, kFg);
  lv_label_set_text(batt_bolt_, "+");
  lv_obj_center(batt_bolt_);
  bar_right_ = text(bar, &buddy_font_11, kMuted);
  lv_label_set_text(bar_right_, "");
  lv_obj_align_to(bar_right_, batt_shell_, LV_ALIGN_OUT_LEFT_MID, -6, 0);
  for (auto* o : {batt_shell_, cap}) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  lv_obj_set_user_data(batt_shell_, cap);

  for (auto* o : {fx_alert_, fx_spark_[0], fx_spark_[1]}) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  for (auto* o : fx_zzz_) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  for (auto* o : fx_wave_) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  refresh();
  show(kFace);
}

void apply(const app::State& state, bool connected) {
  const bool battery_shown = state_.settings.show_battery;
  state_ = state;
  if (state_.settings.show_battery != battery_shown) set_battery(battery_, charging_);
  connected_ = connected;
  pomo_secs_ = state.pomodoro.ends_in;
  pomo_at_ = millis();
  if (state.event.kind == "done") {
    done_session_ = state.event.session;
    done_at_ = millis();
  }
  refresh();
  route();
}

void on_notice(const std::string& t) { on_joke(t); }

void on_shake() {
  dizzy_until_ = millis() + 3000;  // or until the joke arrives
  refresh();
}

void on_joke(const std::string& t) {
  if (dizzy_until_ && dizzy_until_ > millis() + 400) dizzy_until_ = millis() + 400;
  if (view_ != kApprove) show(kFace);
  lv_label_set_text(bubble_text_, t.c_str());
  lv_obj_remove_flag(bubble_, LV_OBJ_FLAG_HIDDEN);

  bubble_until_ = millis() + 15000;
}

void set_link(const char* status, bool connected) {
  // Only a hub connection that drops makes the Buddy offline. Failed attempts
  // must not: another link (the serial bridge) may be delivering the state.
  static bool was_connected = false;
  link_status_ = status;
  if (connected) {
    was_connected = true;
  } else if (was_connected) {
    was_connected = false;
    connected_ = false;
    refresh();
    route();
  }
}

void set_setup_handler(void (*handler)()) { setup_handler_ = handler; }

void set_dictation_handlers(void (*start)(), void (*stop)(const std::string&)) {
  dictate_start_ = start;
  dictate_stop_ = stop;
}

void set_listening(bool on) {
  listening_ = on;
  refresh();
  lv_refr_now(nullptr);
}

void show_message(const char* line1, const char* line2) {
  lv_obj_t* layer = lv_layer_top();
  lv_obj_clean(layer);
  if (!*line1 && !*line2) {  // empty message: just remove the overlay
    lv_refr_now(nullptr);
    return;
  }
  lv_obj_t* bg = box(layer, 0, 0, 320, 240, kBg);
  lv_obj_t* a = text(bg, &buddy_font_13, kFg);
  lv_label_set_text(a, line1);
  lv_obj_align(a, LV_ALIGN_CENTER, 0, -12);
  lv_obj_t* b = text(bg, &buddy_font_11, kMuted);
  lv_label_set_text(b, line2);
  lv_obj_align(b, LV_ALIGN_CENTER, 0, 12);
  lv_refr_now(nullptr);  // the portal blocks the loop: draw now
}

void set_battery(int percent, bool charging) {
  battery_ = percent;
  charging_ = charging;
  auto* cap = static_cast<lv_obj_t*>(lv_obj_get_user_data(batt_shell_));
  if (percent < 0 || !state_.settings.show_battery) {
    lv_label_set_text(bar_right_, "");
    lv_obj_add_flag(batt_shell_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(cap, LV_OBJ_FLAG_HIDDEN);
    return;
  }
  lv_obj_remove_flag(batt_shell_, LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(cap, LV_OBJ_FLAG_HIDDEN);
  const lv_color_t c = percent <= 15 ? kBad : percent <= 40 ? kWarn : kOk;
  lv_obj_set_width(batt_fill_, 18 * (percent > 100 ? 100 : percent) / 100);
  lv_obj_set_style_bg_color(batt_fill_, c, 0);
  if (charging) lv_obj_remove_flag(batt_bolt_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(batt_bolt_, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text_fmt(bar_right_, "%d%%", percent);
  lv_obj_set_style_text_color(bar_right_, percent <= 15 && !charging ? kBad : kMuted, 0);
  lv_obj_align_to(bar_right_, batt_shell_, LV_ALIGN_OUT_LEFT_MID, -6, 0);
  refresh();
}

const app::Settings& settings() { return state_.settings; }

void set_mic_handlers(const MicHandlers& handlers) { mic_ = handlers; }

void set_power_handlers(const PowerHandlers& handlers) { power_ = handlers; }

void toggle_power_menu() {
  if (power_menu_) close_power_menu();
  else open_power_menu();
}

void show_transcript(bool ok, const std::string& text) {
  if (view_ != kPick) return;
  if (!ok || text.empty()) {
    lv_label_set_text(pick_text_, ok ? "não ouvi nada" : ("não consegui transcrever. " + text).c_str());
    pick_back_at_ = millis() + 2500;
    return;
  }
  pick_transcript_ = text;
  fill_pick();
}

void on_send_result(bool ok, const std::string& detail) {
  if (view_ != kPick) return;
  lv_label_set_text_fmt(pick_text_, "%s %s", ok ? "✓" : "✕", detail.c_str());
  pick_back_at_ = millis() + (ok ? 1500 : 3000);
}

void set_stats(const Stats& st) {
  if (view_ != kSettings || page_ != kSystemPage || !stats_values_) return;
  lv_mem_monitor_t mem;
  lv_mem_monitor(&mem);
  char up[24];
  const uint32_t h = st.uptime_s / 3600, m = st.uptime_s / 60 % 60;
  if (h) snprintf(up, sizeof(up), "%luh %02lumin", (unsigned long)h, (unsigned long)m);
  else snprintf(up, sizeof(up), "%lumin %02lus", (unsigned long)m, (unsigned long)(st.uptime_s % 60));
  const char* signal = st.rssi == 0 ? "desconectado" : st.rssi > -60 ? "ótimo" : st.rssi > -70 ? "bom" : st.rssi > -80 ? "fraco" : "ruim";
  lv_label_set_text_fmt(stats_values_,
                        "%d%%\n%lu de %lu KB livres\n%lu de %lu KB livres\n%d%% usada, painel %s\n%lu de %lu KB\n%d dBm (%s)\n%s\n%s (reset %d)",
                        st.cpu, (unsigned long)(st.heap_free / 1024), (unsigned long)(st.heap_total / 1024),
                        (unsigned long)(st.psram_free / 1024), (unsigned long)(st.psram_total / 1024),
                        (int)mem.used_pct, hal::board::panel_variant(), (unsigned long)(st.firmware / 1024), (unsigned long)(st.firmware_max / 1024),
                        st.rssi, signal, st.ip.empty() ? "—" : st.ip.c_str(), up, hal::board::reset_reason());
}

void loop() {
  animate();
  tick_rec();
  if (power_menu_ && millis() - power_menu_at_ > 10000) close_power_menu();
  if (pick_back_at_ && millis() > pick_back_at_) {
    pick_back_at_ = 0;
    if (view_ == kPick) show(kFace);
  }
  tick_approve_timer();
  const uint32_t idle = lv_display_get_inactive_time(nullptr);
  if ((view_ == kMenu && idle > kMenuIdleMs) ||
      ((view_ == kList || view_ == kSession || view_ == kSettings || view_ == kPomo) && idle > kIdleBackMs))
    show(kFace);
  static uint32_t last = 0;
  if (millis() - last > 1000) {  // expire "done", tick the pomodoro countdown, etc.
    last = millis();
    refresh();
    if (view_ == kPomo) fill_pomo();
    if (view_ == kMenu) fill_menu();
    if (state_.pomodoro.phase.empty()) {
      // No pomodoro: "em reunião", or the next meeting within the hour.
      const int left = state_.next_meeting_in - static_cast<int>((millis() - pomo_at_) / 1000);
      if (state_.in_meeting)
        lv_label_set_text(bar_mid_, "em reunião");
      else if (!state_.next_meeting.empty() && state_.next_meeting_in >= 0 && left <= 3600)
        lv_label_set_text_fmt(bar_mid_, "%s em %d min", state_.next_meeting.c_str(), left > 60 ? left / 60 : 1);
      else
        lv_label_set_text(bar_mid_, "");
    } else {
      const int left = pomo_left();
      lv_label_set_text_fmt(bar_mid_, "%s %02d:%02d", state_.pomodoro.phase == "focus" ? "foco" : "pausa", left / 60,
                            left % 60);
    }
  }
}

}  // namespace ui
