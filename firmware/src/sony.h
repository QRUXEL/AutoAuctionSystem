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
  // Press (true) or release (false) the remote's C1 button, which works as the camera's C1 key.
  // With Recall Custom Hold on C1, the camera uses the saved settings while it is held.
  bool holdC1(bool down);
  // Sets the camera's date and time (and location) over the location service, like Sony's app.
  bool setClock(time_t now, String &err);

 private:
  bool send(uint8_t a, uint8_t b);
  NimBLEClient *client_ = nullptr;
  NimBLERemoteCharacteristic *command_ = nullptr;
};
