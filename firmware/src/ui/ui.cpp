#include "ui.h"

#include <Arduino.h>
#include <lvgl.h>

#include <vector>

#include "../app/mood.h"
#include "../app/protocol.h"
#include "sprite.h"

LV_FONT_DECLARE(buddy_font_11);
LV_FONT_DECLARE(buddy_font_13);

namespace ui {
namespace {

constexpr int kUnit = 10;               // 1 sprite unit = 10 px, like the simulator
constexpr int kSpriteX = 70, kSpriteY = 52;
const lv_color_t kBg = lv_color_hex(0x0e0f12), kFg = lv_color_hex(0xf2efe9), kMuted = lv_color_hex(0xb9b4ad),
                 kAccent = lv_color_hex(0xd97757), kOff = lv_color_hex(0x5a5550), kWarn = lv_color_hex(0xe8b04a),
                 kOk = lv_color_hex(0x4caf7a), kBar = lv_color_hex(0x1b1d22);

Sender send_ = nullptr;
app::State state_;
bool connected_ = false;

lv_obj_t *face_, *sprite_, *label_, *weather_, *bubble_, *bubble_text_, *bar_left_;
lv_obj_t *spin_row_ = nullptr, *glyph_ = nullptr, *verb_ = nullptr;
lv_obj_t *fx_alert_, *fx_zzz_, *fx_spark_[2];
std::vector<lv_obj_t*> body_, legs_a_, legs_b_, eyes_;

app::Mood mood_ = app::Mood::Off;
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
  lv_obj_set_size(sprite_, sprite::kCols * kUnit, 10 * kUnit);
  lv_obj_set_pos(sprite_, kSpriteX, kSpriteY);
  lv_obj_remove_flag(sprite_, LV_OBJ_FLAG_CLICKABLE);
  for (int y = 0; y < sprite::kBodyRows; y++) {  // one rect per horizontal run
    const char* row = sprite::kBody[y];
    for (int x = 0; x < sprite::kCols;) {
      if (row[x] != '#') { x++; continue; }
      int end = x;
      while (end < sprite::kCols && row[end] == '#') end++;
      body_.push_back(box(sprite_, x * kUnit, y * kUnit, (end - x) * kUnit, kUnit, kAccent));
      x = end;
    }
  }
  for (auto& l : sprite::kLegsA) legs_a_.push_back(box(sprite_, l[0] * kUnit, l[1] * kUnit, kUnit, 2 * kUnit, kAccent));
  for (auto& l : sprite::kLegsB) legs_b_.push_back(box(sprite_, l[0] * kUnit, l[1] * kUnit, kUnit, 2 * kUnit, kAccent));
}

void draw_eyes(const sprite::Eyes& e) {
  for (auto* o : eyes_) lv_obj_delete(o);
  eyes_.clear();
  for (int i = 0; i < e.count; i++) {
    const auto& r = e.rects[i];
    eyes_.push_back(box(sprite_, r.x * kUnit, r.y * kUnit, r.w * kUnit, r.h * kUnit, kBg));
  }
}

const sprite::Eyes& eyes_for(app::Mood m) {
  switch (m) {
    case app::Mood::Wait: return sprite::kEyesWide;
    case app::Mood::Done: return sprite::kEyesHappy;
    case app::Mood::Sleep: return sprite::kEyesSleep;
    case app::Mood::Off: return sprite::kEyesDead;
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
  if (on && !glyph_) {
    lv_label_set_text(label_, "");
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

void set_mood(const app::MoodView& v) {
  if (v.mood != mood_) {
    mood_ = v.mood;
    draw_eyes(eyes_for(mood_));
    color_sprite(mood_ == app::Mood::Off ? kOff : kAccent);
    show_spinner(mood_ == app::Mood::Work);
    lv_obj_set_y(sprite_, kSpriteY);
    if (mood_ == app::Mood::Sleep) lv_obj_remove_flag(fx_zzz_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(fx_zzz_, LV_OBJ_FLAG_HIDDEN);
    if (mood_ == app::Mood::Wait) lv_obj_remove_flag(fx_alert_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(fx_alert_, LV_OBJ_FLAG_HIDDEN);
    for (auto* s : fx_spark_)
      if (mood_ == app::Mood::Done) lv_obj_remove_flag(s, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(s, LV_OBJ_FLAG_HIDDEN);
  }
  if (mood_ != app::Mood::Work) lv_label_set_text(label_, v.label.c_str());
}

void hide_bubble() {
  bubble_until_ = 0;
  lv_obj_add_flag(bubble_, LV_OBJ_FLAG_HIDDEN);
  lv_obj_set_x(sprite_, kSpriteX);
}

void refresh() {
  if (done_at_ && millis() - done_at_ > 8000) { done_at_ = 0; done_session_.clear(); }
  if (bubble_until_ && (state_.night || !connected_)) hide_bubble();
  set_mood(app::mood(state_, connected_, false, done_session_));

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

void animate() {
  if (millis() - frame_at_ < 120) return;
  frame_at_ = millis();
  frame_++;
  if (glyph_) {
    lv_label_set_text(glyph_, kSpin[frame_ % 10]);
    if (millis() - verb_at_ > 8000) { verb_text_ = kVerbs[random(sizeof(kVerbs) / sizeof(*kVerbs))]; verb_at_ = millis(); }
    lv_label_set_text_fmt(verb_, "%s…", verb_text_);
  }
  const bool step = (frame_ / 2) % 2;  // ~4 Hz
  int dy = 0;
  if (mood_ == app::Mood::Work) dy = step ? -kUnit / 2 : 0;
  if (mood_ == app::Mood::Wait || mood_ == app::Mood::Talk) dy = step ? -kUnit : 0;
  lv_obj_set_y(sprite_, kSpriteY + dy);
  for (auto* l : legs_a_) lv_obj_set_y(l, 8 * kUnit - (mood_ == app::Mood::Work && step ? kUnit : 0));
  for (auto* l : legs_b_) lv_obj_set_y(l, 8 * kUnit - (mood_ == app::Mood::Work && !step ? kUnit : 0));
  if (bubble_until_ && millis() > bubble_until_) hide_bubble();
}

void on_face_tap(lv_event_t*) {
  if (send_) send_(app::touch());
}

}  // namespace

void begin(Sender send) {
  send_ = send;
  lv_obj_t* scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, kBg, 0);
  lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  face_ = lv_obj_create(scr);
  lv_obj_remove_style_all(face_);
  lv_obj_set_size(face_, 320, 218);
  lv_obj_add_event_cb(face_, on_face_tap, LV_EVENT_CLICKED, nullptr);
  build_sprite();

  label_ = text(face_, &buddy_font_11, kMuted);
  lv_obj_set_width(label_, 304);
  lv_obj_set_style_text_align(label_, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_long_mode(label_, LV_LABEL_LONG_DOT);
  lv_obj_align(label_, LV_ALIGN_TOP_MID, 0, 176);

  weather_ = text(face_, &buddy_font_11, kMuted);
  lv_obj_set_pos(weather_, 8, 6);

  fx_alert_ = text(face_, &buddy_font_13, kWarn);
  lv_label_set_text(fx_alert_, "!");
  lv_obj_set_pos(fx_alert_, kSpriteX + 85, kSpriteY - 34);
  fx_zzz_ = text(face_, &buddy_font_13, lv_color_hex(0x8a857f));
  lv_label_set_text(fx_zzz_, "z Z");
  lv_obj_set_pos(fx_zzz_, kSpriteX + 150, kSpriteY - 30);
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

  for (auto* o : {fx_alert_, fx_zzz_, fx_spark_[0], fx_spark_[1]}) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  mood_ = app::Mood::Idle;  // force the first set_mood() to draw everything
  refresh();
}

void apply(const app::State& state, bool connected) {
  state_ = state;
  connected_ = connected;
  if (state.event.kind == "done") {
    done_session_ = state.event.session;
    done_at_ = millis();
  }
  refresh();
}

void on_joke(const std::string& t) {
  lv_label_set_text(bubble_text_, t.c_str());
  lv_obj_remove_flag(bubble_, LV_OBJ_FLAG_HIDDEN);
  lv_obj_set_x(sprite_, kSpriteX - 34);  // step aside for the bubble
  bubble_until_ = millis() + 15000;
}

void loop() {
  animate();
  static uint32_t last = 0;
  if (millis() - last > 1000) { last = millis(); refresh(); }  // expire "done", etc.
}

}  // namespace ui
