// edelkrone HeadONE over Bluetooth LE, standing in for the edelkrone Python SDK's
// connect / status / joystick / stop on a lone head mounted for pan.
#pragma once
#include <Arduino.h>
#include <NimBLEDevice.h>

#include "config.h"
#include "edelkrone_proto.h"

class HeadOne {
 public:
  // Turn behaviour; starts from config.h, changed at run time from the settings page.
  struct Tuning {
    bool clockwise;
    float headPerTable, slowZone, minSpeed, stopEarly;
    uint32_t stallMs;
  };
  Tuning tuning{TABLE_CLOCKWISE != 0, HEAD_DEG_PER_TABLE_DEG, SLOW_ZONE_DEG, MIN_TURN_SPEED, STOP_EARLY_DEG, STALL_TIMEOUT_MS};

  static bool isHeadOne(NimBLEAdvertisedDevice *dev);

  void begin();
  bool connect(const NimBLEAddress &address, const std::string &advData, String &err);
  bool connected();
  void disconnect();

  bool refreshStatus(ek::Status &out);
  bool readPan(double &deg);
  // Turns the table by tableDeg degrees (negative = backwards), driving the joystick and watching the angle.
  bool turnBy(float tableDeg, float speed, volatile bool &abort, String &err);
  // One joystick command, -1..1 of top speed (positive = the capture direction); 0 lets go.
  bool drive(float speed);
  // Table angle in degrees from the zero mark, 0..360, followed from every position reading.
  float angle() const;
  // Zero mark at the current position. Kept as the head's own position reading, which survives a
  // station restart, so it can be saved and restored with zeroPan() / restoreZero().
  void setZero() { zeroPan_ = pan_; table_ = 0; zero_ = 0; }
  double zeroPan() const { return zeroPan_; }
  void restoreZero(double pan) { zeroPan_ = pan; havePan_ = false; }
  // Experimental planned move with the head's own numeric move; reports what happened.
  bool numericMove(float deg, String &report);
  void stop();
  bool fireShutter();

  int battery() const { return battery_; }
  double lastPan() const { return pan_; }
  uint16_t mtu();
  uint32_t badFrames() const { return badFrames_; }

 private:
  enum Ch { DATA = 0, IOCTL = 1 };
  bool exchange(Ch ch, const ek::Packet &p, int expectType, uint32_t timeoutMs);
  void onNotify(Ch ch, const uint8_t *data, size_t len);

  NimBLEClient *client_ = nullptr;
  NimBLERemoteCharacteristic *write_[2] = {nullptr, nullptr};
  NimBLERemoteCharacteristic *read_[2] = {nullptr, nullptr};
  SemaphoreHandle_t mutex_ = nullptr, reply_ = nullptr;
  volatile int waitCh_ = -1, waitType_ = -1;
  ek::Status status_{};
  volatile double pan_ = 0;
  volatile double table_ = 0, zero_ = 0;   // table degrees, summed from pan changes
  volatile bool havePan_ = false;
  volatile double zeroPan_ = NAN;          // head reading at the zero mark; NAN = not set
  volatile uint16_t group_ = ek::NO_GROUP;
  volatile int battery_ = -1;
  volatile uint32_t badFrames_ = 0;
};
