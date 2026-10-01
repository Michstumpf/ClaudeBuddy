// LVGL 9 configuration for the Claude Buddy (320x240, RGB565).
// Only what differs from LVGL's defaults (lv_conf_internal.h) is set here.
#if 1
#ifndef LV_CONF_H
#define LV_CONF_H

#define LV_COLOR_DEPTH 16
#define LV_USE_STDLIB_MALLOC LV_STDLIB_BUILTIN
#define LV_MEM_SIZE (96 * 1024U)
#define LV_DEF_REFR_PERIOD 30
#define LV_DPI_DEF 130
#define LV_USE_OS LV_OS_NONE
#define LV_USE_LOG 0

// Built-in fonts are ASCII only; the UI uses DejaVu Sans Mono with Latin-1
// (ã, ç, é…) and the few symbols it needs, generated into src/ui/fonts.
#define LV_FONT_MONTSERRAT_14 0
#define LV_FONT_CUSTOM_DECLARE LV_FONT_DECLARE(buddy_font_11) LV_FONT_DECLARE(buddy_font_13)
#define LV_FONT_DEFAULT &buddy_font_11

#define LV_USE_THEME_DEFAULT 1
#define LV_THEME_DEFAULT_DARK 1

#endif  // LV_CONF_H
#endif
