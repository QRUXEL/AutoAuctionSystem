// Sony Alpha Bluetooth remote (the protocol the RMT-P1BT speaks): service
// 8000FF00-FF00-FFFF-FFFF-FFFFFFFFFFFF, two-byte commands written to FF01.
// On the camera: Network > Bluetooth > Bluetooth Function On, Bluetooth Rmt Ctrl On,
// and Pairing the first time.
#pragma once
#include <Arduino.h>
#include <NimBLEDevice.h>

class SonyRemote {
 public:
  static bool isSonyCamera(NimBLEAdvertisedDevice *dev);
  bool connect(const NimBLEAddress &address, String &err);
  bool connected();
  bool takePhoto();
  bool toggleRecord();

 private:
  bool send(uint8_t a, uint8_t b);
  NimBLEClient *client_ = nullptr;
  NimBLERemoteCharacteristic *command_ = nullptr;
};
