// edelkrone HeadONE packet layer, ported from the edelkrone Python SDK 0.1.4 (MIT licence,
// Copyright (c) 2026 edelkrone): protocol/_generated/head_module.py and protocol/_generated/moco.py.
// Plain C++ with no Arduino dependency, so it can be unit-tested on a PC (see test/).
//
// Frame layout, both directions: [len][type][payload...][checksum hi][checksum lo]
//   Outgoing: byte 0 is the length of everything before the checksum; the checksum is
//   the 16-bit big-endian sum of those bytes. Total size = byte0 + 2.
//   Incoming: byte 0 is the reply type; the last two bytes are the sum of all bytes before them.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace ek {

// BLE identity (devices/models.py): HeadONE V1 advertises "HeadOne", HeadONE V2 "HeadOneFM".
static const char *const NAME_V1 = "HeadOne";
static const char *const NAME_V2 = "HeadOneFM";
static const char *const DATA_WRITE  = "5a861ccb-687b-459a-af01-347792f07a0c";
static const char *const DATA_READ   = "c2720639-bdfc-4b8e-896d-b5bea0479976";
static const char *const IOCTL_WRITE = "7da5b2e6-071c-4601-9fde-ecaf291d0a04";
static const char *const IOCTL_READ  = "eaa8212a-3551-4e98-adeb-0b68957a215a";

// Reply types on the DATA channel (LibHeadModuleMk2BleResponse)
enum : uint8_t { R_GENERAL_STATUS = 2, R_NUMERIC_READOUT = 18 };
// Reply types on the IO_CTRL channel (LibEdelkroneMocoResponse)
enum : uint8_t { R_IOCTL_STATUS = 0 };

// Head states (LibHeadModuleMk2HeadState) that mean "moving"
enum : uint8_t { ST_IDLE = 0, ST_KEYPOSE_MOVING = 5, ST_NUMERIC_MOVE_RUNNING = 12 };
// Layouts (EdlSetup)
enum : uint8_t { SETUP_NONE = 0, SETUP_PAN_ONLY = 1, SETUP_TILT_ONLY = 2 };

static const uint8_t CURRENT_POSITION = 255;   // CommandSet.CURRENT_POSITION_INDEX
static const uint16_t NO_GROUP = 0xFFFF;

struct Packet {
  uint8_t data[16];
  size_t len;
};

inline void put16(uint8_t *p, size_t at, int32_t v) { p[at] = (v >> 8) & 0xFF; p[at + 1] = v & 0xFF; }
inline void put32(uint8_t *p, size_t at, int32_t v) {
  p[at] = (v >> 24) & 0xFF; p[at + 1] = (v >> 16) & 0xFF; p[at + 2] = (v >> 8) & 0xFF; p[at + 3] = v & 0xFF;
}
inline int32_t get32s(const uint8_t *p, size_t at) {
  return (int32_t)(((uint32_t)p[at] << 24) | ((uint32_t)p[at + 1] << 16) | ((uint32_t)p[at + 2] << 8) | p[at + 3]);
}
inline uint16_t get16u(const uint8_t *p, size_t at) { return (uint16_t)((p[at] << 8) | p[at + 1]); }

// Builds a packet: body holds byte0 (length) onward, without the checksum.
inline Packet frame(const uint8_t *body, uint8_t declared) {
  Packet pk{};
  uint32_t sum = 0;
  for (uint8_t i = 0; i < declared; i++) { pk.data[i] = body[i]; sum += body[i]; }
  put16(pk.data, declared, (int32_t)(sum & 0xFFFF));
  pk.len = declared + 2u;
  return pk;
}

// Python's int(x) truncates toward zero; so does a C cast.
inline int32_t speed16(float rel) {
  if (rel > 1) rel = 1;
  if (rel < -1) rel = -1;
  return (int32_t)(rel * 32000.0f);
}

// ---- DATA channel commands (head_module.py) ----
inline Packet statusGet()             { const uint8_t b[] = {2, 1}; return frame(b, 2); }
inline Packet stopMove()              { const uint8_t b[] = {2, 9}; return frame(b, 2); }        // keypose_abort
inline Packet triggerShutter()        { const uint8_t b[] = {2, 59}; return frame(b, 2); }       // trigger_photo_test
inline Packet configurationSet(uint8_t setup) { const uint8_t b[] = {3, 201, setup}; return frame(b, 3); }
inline Packet numericRead(uint8_t index) { const uint8_t b[] = {4, 83, index, 0}; return frame(b, 4); }

inline Packet manualPanTilt(float pan, float tilt) {
  uint8_t b[6] = {6, 10};
  put16(b, 2, speed16(pan));
  put16(b, 4, speed16(tilt));
  return frame(b, 6);
}

// Planned move of the pan axis to an angle (numeric_control_pan_move). Not used by
// the SDK's public API in 0.1.4, so treated as experimental here.
inline Packet numericPanMove(float deg) {
  uint8_t b[7] = {7, 85, 0};
  put32(b, 3, (int32_t)(deg * 1000.0f));
  return frame(b, 7);
}

// ---- IO_CTRL channel commands (moco.py) ----
inline Packet ioctlStatusGet() { const uint8_t b[] = {2, 2}; return frame(b, 2); }
inline Packet groupIdSelfSet(uint16_t group) {
  uint8_t b[4] = {4, 9};
  put16(b, 2, group);
  return frame(b, 4);
}

// ---- replies ----
inline bool checksumOk(const uint8_t *p, size_t n) {
  if (n < 3) return false;
  uint32_t sum = 0;
  for (size_t i = 0; i < n - 2; i++) sum += p[i];
  return (sum & 0xFFFF) == get16u(p, n - 2);
}

struct Status {           // from a GENERAL_STATUS reply (status_parse)
  uint8_t state;          // ptr[1]
  uint8_t configuration;  // ptr[6]
  uint8_t battery;        // ptr[17] & 0xF, as percent
  bool headCalibrated;    // ptr[3] bit 0
};

inline bool parseStatus(const uint8_t *p, size_t n, Status &s) {
  if (n < 20 || p[0] != R_GENERAL_STATUS) return false;
  s.state = p[1];
  s.configuration = p[6];
  s.battery = (uint8_t)((p[17] & 0xF) * 100 / 15);
  s.headCalibrated = p[3] & 1;
  return true;
}

// From a NUMERIC_VALUE_READOUT reply (numeric_control_parse). Returns false for other slots.
inline bool parsePanDeg(const uint8_t *p, size_t n, uint8_t index, double &pan) {
  if (n < 8 || p[0] != R_NUMERIC_READOUT || p[1] != index) return false;
  pan = get32s(p, 2) / 1000.0;
  return true;
}

inline bool parseGroupId(const uint8_t *p, size_t n, uint16_t &group) {
  if (n < 5 || p[0] != R_IOCTL_STATUS) return false;
  group = get16u(p, 1);
  return true;
}

// Advertisement service data (adv_parse): byte 0 = layout, byte 1 bit 0 = mounted tilted.
inline bool advTilted(const uint8_t *adv, size_t n) { return n >= 2 && (adv[1] & 1); }

}  // namespace ek
