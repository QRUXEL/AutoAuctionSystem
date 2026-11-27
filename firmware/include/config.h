#pragma once

// Wi-Fi password, OTA password and station key live in secrets.h, which is not in git.
// Copy secrets.example.h to secrets.h and fill it in.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy include/secrets.example.h to include/secrets.h and fill it in"
#endif

// ---- network ----
#define HOSTNAME       "auction-station"   // also reachable as http://auction-station.local

// The auction server on the local Coolify (use its LAN address and port, plain http)
#define SERVER_URL     "http://10.8.0.247:8095"

// ---- pins (ESP32-S3-DevKitC-1) ----
#define BUTTON_PIN     0      // BOOT button; wire a big button between this pin and GND
#define LED_RGB_PIN    48     // on-board RGB LED (38 on some DevKitC v1.1 boards); -1 for none

// ---- Bluetooth ----
// Only use this camera (lowercase address from GET /devices); "" = the first Sony camera seen.
// Set it when more than one Sony camera is in range.
#define CAMERA_ADDRESS "9c:50:d1:c4:bb:ea"   // ILCE-7M4

// ---- clock (for setting the camera's date and time) ----
#define NTP_SERVER     "pool.ntp.org"
#define TIMEZONE       "CET-1CEST,M3.5.0,M10.5.0/3"   // POSIX TZ: Sweden / Central Europe with summer time
// The camera takes the time together with a location (as from Sony's phone app). Leave both 0 to
// send the time only: the station then switches the camera's location link off again right after,
// so photos are not tagged with 0,0.
#define STATION_LAT    0.0
#define STATION_LON    0.0

// ---- capture sequence ----
#define PHOTOS                4
#define RECORD_VIDEO          1      // 1 = film one full turn after the photos
#define SHUTTER_VIA_HEAD      0      // 1 = fire stills through the HeadONE's camera port (cable) instead of Bluetooth
#define SETTLE_BEFORE_PHOTO_MS 600   // let the item stop wobbling
#define WAIT_AFTER_PHOTO_MS   1200   // exposure + card write before the table moves
#define VIDEO_LEAD_MS         1000   // still footage before and after the spin

// ---- turntable ----
#define TABLE_CLOCKWISE       1      // flip if the table turns the wrong way
#define HEAD_DEG_PER_TABLE_DEG 1.0f  // head pan degrees per degree of table turn; calibrate with /table/jog
#define PHOTO_TURN_SPEED      0.5f   // fraction of the head's top speed
#define VIDEO_TURN_SPEED      0.25f
#define SLOW_ZONE_DEG         12.0f  // start slowing this many degrees before the target
#define MIN_TURN_SPEED        0.06f
#define STOP_EARLY_DEG        0.4f   // raise if the table overshoots, lower if it stops short
#define STALL_TIMEOUT_MS      4000   // give up if the reported angle stops changing
