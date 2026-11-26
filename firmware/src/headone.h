// edelkrone HeadONE over Bluetooth LE, standing in for the edelkrone Python SDK's
// connect / status / joystick / stop on a lone head mounted for pan.
#pragma once
#include <Arduino.h>
#include <NimBLEDevice.h>

#include "edelkrone_proto.h"

class HeadOne {
 public:
  static bool isHeadOne(NimBLEAdvertisedDevice *dev);

  void begin();
  bool connect(const NimBLEAddress &address, const std::string &advData, String &err);
  bool connected();
  void disconnect();

  bool refreshStatus(ek::Status &out);
  bool readPan(double &deg);
  // Turns the table by tableDeg degrees (positive), driving the joystick and watching the angle.
  bool turnBy(float tableDeg, float speed, volatile bool &abort, String &err);
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
  volatile uint16_t group_ = ek::NO_GROUP;
  volatile int battery_ = -1;
  volatile uint32_t badFrames_ = 0;
};
