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

enum View : uint8_t { kFace, kList, kSession, kApprove, kSettings, kViews };
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

const lv_color_t kBox = lv_color_hex(0x181a1f), kBtn = lv_color_hex(0x3a3f48), kBtnOk = lv_color_hex(0x2f8a5b),
                 kBtnBad = lv_color_hex(0xb8423b), kBtnInfo = lv_color_hex(0x3c6fc4), kBad = lv_color_hex(0xe0574f),
                 kInfo = lv_color_hex(0x6aa8ff);
lv_obj_t *list_count_, *list_rows_, *sess_title_, *sess_msg_, *appr_timer_, *appr_title_, *appr_danger_, *appr_body_;
constexpr int kPrefs = 9;
lv_obj_t* pref_btn_[kPrefs];  // jokes, joke_voice, interval, weather, skin, battery, long task, pomodoro, wifi
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
lv_obj_t *fx_alert_, *fx_zzz_, *fx_spark_[2];
std::vector<lv_obj_t*> body_, legs_a_, legs_b_, eyes_, big_eyes_;
// Skin "eyes": only the eyes, big, on a body-colored screen (for a case shaped
// like the mascot). Same eye shapes, 24 px units, sprite columns 3..14.
constexpr int kBigUnit = 24, kBigX = 16, kBigY = 34;
lv_obj_t* bigeyes_ = nullptr;
bool eyes_skin_ = false;

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
  for (auto* o : big_eyes_) lv_obj_delete(o);
  eyes_.clear();
  big_eyes_.clear();
  for (int i = 0; i < e.count; i++) {
    const auto& r = e.rects[i];
    eyes_.push_back(box(sprite_, r.x * kUnit, r.y * kUnit, r.w * kUnit, r.h * kUnit, kBg));
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

void set_mood(const app::MoodView& v) {
  if (v.mood != mood_ || redraw_) {
    redraw_ = false;
    mood_ = v.mood;
    draw_eyes(eyes_for(mood_));
    color_sprite(mood_ == app::Mood::Off ? kOff : kAccent);
    show_spinner(mood_ == app::Mood::Work);
    lv_obj_set_y(sprite_, kSpriteY);
    // The effects belong to the whole mascot; the eyes skin shows state by color.
    const bool fx = !eyes_skin_;
    if (fx && mood_ == app::Mood::Sleep) lv_obj_remove_flag(fx_zzz_, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(fx_zzz_, LV_OBJ_FLAG_HIDDEN);
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
  if (!eyes_skin_) lv_obj_set_x(sprite_, kSpriteX);
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
  if (listening_) v = {app::Mood::Talk, "ouvindo… solte para enviar"};
  set_mood(v);

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
  const int look[] = {0, 0, -1, -1, 0, 0, 1, 1};  // like the simulator's "look" animation
  lv_obj_set_x(bigeyes_, kBigX + (mood_ == app::Mood::Work ? look[(frame_ / 3) % 8] * kBigUnit : 0));
  for (auto* l : legs_a_) lv_obj_set_y(l, 8 * kUnit - (mood_ == app::Mood::Work && step ? kUnit : 0));
  for (auto* l : legs_b_) lv_obj_set_y(l, 8 * kUnit - (mood_ == app::Mood::Work && !step ? kUnit : 0));
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
  show(kList);
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
  button(v, "◀ Buddy", kBtn, go, to(kFace));
  lv_obj_set_pos(lv_obj_get_child(v, -1), 4, 4);
  list_count_ = text(v, &buddy_font_11, lv_color_hex(0x7d776f));
  lv_obj_set_pos(list_count_, 86, 12);
  lv_obj_set_width(list_count_, 190);
  lv_label_set_long_mode(list_count_, LV_LABEL_LONG_DOT);
  button(v, "⚙", kBtn, go, to(kSettings));
  lv_obj_align(lv_obj_get_child(v, -1), LV_ALIGN_TOP_RIGHT, -4, 4);
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

// ---- settings ----

const int kIntervals[] = {15, 30, 45, 60, 120};

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
    case 7:
      if (send_) send_(app::pomodoro(state_.pomodoro.phase.empty()));
      return;
    case 8:
      if (setup_handler_) setup_handler_();
      return;
  }
  if (send_) send_(app::settings(s));
}

void on_joke_now(lv_event_t*) {
  if (settling()) return;
  if (send_) send_(app::joke_now());
  show(kFace);
}

void build_settings(lv_obj_t* v) {
  lv_obj_t* title = text(v, &buddy_font_13, kFg);
  lv_label_set_text(title, "⚙ Configurações");
  lv_obj_set_pos(title, 8, 8);
  const char* names[kPrefs] = {"Piadas de vez em quando", "Falar as piadas", "Intervalo entre piadas",
                               "Temperatura lá fora", "Visual", "Indicador de bateria",
                               "Aviso de tarefa longa", "Pomodoro (25 + 5 min)", "WiFi e hub"};
  // The options scroll; Voltar / Contar stay put at the bottom.
  lv_obj_t* list = lv_obj_create(v);
  lv_obj_remove_style_all(list);
  lv_obj_set_pos(list, 8, 28);
  lv_obj_set_size(list, 304, 146);
  lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(list, 3, 0);
  lv_obj_add_flag(list, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_scrollbar_mode(list, LV_SCROLLBAR_MODE_ACTIVE);
  for (int i = 0; i < kPrefs; i++) {
    lv_obj_t* row = text_box(list, 0, 0, 304, 26);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_ver(row, 2, 0);
    lv_obj_t* l = text(row, &buddy_font_11, kFg);
    lv_label_set_text(l, names[i]);
    if (i == 3) weather_label_ = l;
    pref_btn_[i] = button(row, "", kBtn, on_pref, reinterpret_cast<void*>(static_cast<uintptr_t>(i)));
    lv_obj_set_size(pref_btn_[i], 96, 22);
    lv_obj_set_style_pad_ver(pref_btn_[i], 0, 0);
  }
  lv_obj_t* back = button(v, "◀ Voltar", kBtn, go, to(kList));
  lv_obj_set_pos(back, 8, 180);
  lv_obj_t* now = button(v, "Contar uma piada agora", kBtnInfo, on_joke_now);
  lv_obj_align(now, LV_ALIGN_TOP_RIGHT, -8, 180);
}

void fill_settings() {
  const bool on[kPrefs] = {state_.settings.jokes, state_.settings.joke_voice, false, state_.settings.weather, false,
                           state_.settings.show_battery};
  for (int i : {0, 1, 3, 5}) {
    set_button_text(pref_btn_[i], on[i] ? "Ligado" : "Desligado");
    lv_obj_set_style_bg_color(pref_btn_[i], on[i] ? kBtnOk : kBtn, 0);
  }
  char buf[16];
  snprintf(buf, sizeof(buf), "%d min", state_.settings.joke_interval_min);
  set_button_text(pref_btn_[2], buf);
  set_button_text(pref_btn_[4], state_.settings.eyes_skin ? "Só os olhos" : "Clássico");
  if (state_.settings.long_task_min) {
    snprintf(buf, sizeof(buf), "%d min", state_.settings.long_task_min);
    set_button_text(pref_btn_[6], buf);
  } else {
    set_button_text(pref_btn_[6], "Desligado");
  }
  const bool pomo = !state_.pomodoro.phase.empty();
  set_button_text(pref_btn_[7], pomo ? "Parar" : "Iniciar");
  lv_obj_set_style_bg_color(pref_btn_[7], pomo ? kBtnOk : kBtn, 0);
  set_button_text(pref_btn_[8], "Configurar");
  // The city is changed from the web panel (no keyboard on a 2" screen).
  lv_label_set_text_fmt(weather_label_, "Temperatura em %s", state_.settings.city.c_str());
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
  if (v == kApprove && !state_.pending.empty()) fill_approve();
  lv_display_trigger_activity(nullptr);  // restarts the idle-return countdown
}

void route() {
  // Like the simulator: a pending approval takes over the screen; when it is
  // gone, back to the face. Lists left alone go back to the face after 30 s.
  if (!state_.pending.empty() && view_ != kApprove) show(kApprove);
  else if (state_.pending.empty() && view_ == kApprove) { approval_id_.clear(); show(kFace); }
  else if (view_ == kApprove) fill_approve();
  else if (view_ == kList) fill_list();
  else if (view_ == kSession) fill_session();
  else if (view_ == kSettings) fill_settings();
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
  build_sprite();
  bigeyes_ = lv_obj_create(face_);
  lv_obj_remove_style_all(bigeyes_);
  lv_obj_set_pos(bigeyes_, kBigX, kBigY);
  lv_obj_set_size(bigeyes_, 12 * kBigUnit, 5 * kBigUnit);
  lv_obj_remove_flag(bigeyes_, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_flag(bigeyes_, LV_OBJ_FLAG_HIDDEN);

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

  for (auto* o : {fx_alert_, fx_zzz_, fx_spark_[0], fx_spark_[1]}) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
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

void on_joke(const std::string& t) {
  if (view_ != kApprove) show(kFace);
  lv_label_set_text(bubble_text_, t.c_str());
  lv_obj_remove_flag(bubble_, LV_OBJ_FLAG_HIDDEN);
  if (!eyes_skin_) lv_obj_set_x(sprite_, kSpriteX - 34);  // step aside for the bubble
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

void loop() {
  animate();
  tick_approve_timer();
  if ((view_ == kList || view_ == kSession || view_ == kSettings) &&
      lv_display_get_inactive_time(nullptr) > kIdleBackMs)
    show(kFace);
  static uint32_t last = 0;
  if (millis() - last > 1000) {  // expire "done", tick the pomodoro countdown, etc.
    last = millis();
    refresh();
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
      int left = pomo_secs_ - static_cast<int>((millis() - pomo_at_) / 1000);
      if (left < 0) left = 0;
      lv_label_set_text_fmt(bar_mid_, "%s %02d:%02d", state_.pomodoro.phase == "focus" ? "foco" : "pausa", left / 60,
                            left % 60);
    }
  }
}

}  // namespace ui
