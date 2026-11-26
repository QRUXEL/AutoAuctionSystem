#include "sony.h"

static const NimBLEUUID REMOTE_SERVICE("8000FF00-FF00-FFFF-FFFF-FFFFFFFFFFFF");
static const NimBLEUUID REMOTE_COMMAND((uint16_t)0xFF01);

// A Sony camera advertises manufacturer data: company 0x012D, device type 0x0003.
bool SonyRemote::isSonyCamera(NimBLEAdvertisedDevice *dev) {
  if (!dev->haveManufacturerData()) return false;
  std::string m = dev->getManufacturerData();
  return m.size() >= 4 && (uint8_t)m[0] == 0x2D && (uint8_t)m[1] == 0x01 && (uint8_t)m[2] == 0x03 && (uint8_t)m[3] == 0x00;
}

bool SonyRemote::connected() { return client_ && client_->isConnected() && command_; }

bool SonyRemote::connect(const NimBLEAddress &address, String &err) {
  if (!client_) client_ = NimBLEDevice::createClient();
  command_ = nullptr;
  if (!client_->connect(address)) { err = "camera found but would not connect"; return false; }
  if (!client_->secureConnection()) {          // bonds on first use
    err = "camera pairing failed: open Bluetooth > Pairing on the camera";
    client_->disconnect();
    return false;
  }
  NimBLERemoteService *svc = client_->getService(REMOTE_SERVICE);
  command_ = svc ? svc->getCharacteristic(REMOTE_COMMAND) : nullptr;
  if (!command_) {
    err = "camera has no remote service: turn on Bluetooth Rmt Ctrl";
    client_->disconnect();
    return false;
  }
  Serial.println("Camera ready");
  return true;
}

bool SonyRemote::send(uint8_t a, uint8_t b) {
  const uint8_t cmd[2] = {a, b};
  return connected() && command_->writeValue(cmd, 2, true);
}

bool SonyRemote::takePhoto() {
  bool ok = send(0x01, 0x07);   // focus down
  delay(150);
  ok = send(0x01, 0x09) && ok;  // shutter down
  delay(150);
  ok = send(0x01, 0x08) && ok;  // shutter up
  ok = send(0x01, 0x06) && ok;  // focus up
  return ok;
}

bool SonyRemote::toggleRecord() {   // same as pressing the MOVIE button
  bool ok = send(0x01, 0x0F);
  delay(150);
  return send(0x01, 0x0E) && ok;
}
