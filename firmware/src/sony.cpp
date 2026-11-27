#include "sony.h"

#include "config.h"

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
  if (!client_) {
    client_ = NimBLEDevice::createClient();
    client_->setConnectTimeout(10);            // default is 30 s; the camera answers in well under 10
  }
  command_ = nullptr;
  if (!client_->connect(address)) { err = "camera found but would not connect"; return false; }
  // Discovery and secureConnection() wait with no timeout; if the camera never answers,
  // disconnecting from this timer wakes them up with an error.
  esp_timer_handle_t watchdog = nullptr;
  esp_timer_create_args_t args = {};
  args.callback = [](void *c) { static_cast<NimBLEClient *>(c)->disconnect(); };
  args.arg = client_;
  args.name = "sony-pair";
  esp_timer_create(&args, &watchdog);
  esp_timer_start_once(watchdog, 60 * 1000000ULL);   // long enough for a person to press OK on the camera
  auto done = [&](bool ok) { esp_timer_stop(watchdog); esp_timer_delete(watchdog); if (!ok) client_->disconnect(); return ok; };

  // Like the camera's own remote: find the remote service first, pair after.
  NimBLERemoteService *svc = client_->getService(REMOTE_SERVICE);
  Serial.printf("Sony: remote service %s\n", svc ? "found" : "missing");
  command_ = svc ? svc->getCharacteristic(REMOTE_COMMAND) : nullptr;
  if (!command_) {
    err = client_->isConnected() ? "camera has no remote service: turn on Bluetooth Rmt Ctrl"
                                 : "camera stopped answering during discovery";
    return done(false);
  }
  NimBLEConnInfo info = client_->getConnInfo();
  Serial.printf("Sony: encrypted=%d bonded=%d\n", info.isEncrypted(), info.isBonded());
  // One pairing attempt per connection: the camera answers a quick second try with "Repeated Attempts".
  // The FF01 command needs a bonded link (it answers Insufficient Authentication otherwise).
  if (!info.isEncrypted() && !client_->secureConnection()) {   // bonds on first use
    int rc = client_->getLastError();
    if (rc == BLE_HS_ERR_SM_PEER_BASE + BLE_SM_ERR_REPEATED)
      err = "camera says too many pairing attempts: wait a moment";
    else if (!client_->isConnected() || rc == BLE_HS_ENOTCONN)
      err = "camera closed the connection: press OK on the camera if it asks to pair";
    else
      err = "camera pairing failed (rc " + String(rc) + "): open Bluetooth > Pairing on the camera";
    command_ = nullptr;
    return done(false);
  }
  done(true);
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

// ---- date and time, through the location service Sony's phone app uses ----
// Layout from gkoh/furble (MIT, lib/furble/Sony.h): a 95-byte write to DD11, big-endian fields.
static const NimBLEUUID LOCATION_SERVICE("8000DD00-DD00-FFFF-FFFF-FFFFFFFFFFFF");
static const NimBLEUUID LOCATION_INFO((uint16_t)0xDD11), LOCATION_ALLOW((uint16_t)0xDD30), LOCATION_ENABLE((uint16_t)0xDD31);

static bool setFlag(NimBLERemoteService *svc, const NimBLEUUID &uuid, uint8_t v) {
  NimBLERemoteCharacteristic *c = svc->getCharacteristic(uuid);
  if (!c) return false;
  std::string now = c->readValue();
  if (!now.empty() && (uint8_t)now[0] == v) return true;
  return c->writeValue(&v, 1, true);
}

static int64_t daysFromCivil(int y, int m, int d) {   // days since 1970-01-01 (Howard Hinnant)
  y -= m <= 2;
  int64_t era = (y >= 0 ? y : y - 399) / 400;
  int yoe = y - era * 400, doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  return era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
}

bool SonyRemote::setClock(time_t now, String &err) {
  if (!connected()) { err = "camera not connected"; return false; }
  NimBLERemoteService *svc = client_->getService(LOCATION_SERVICE);
  if (!svc || !svc->getCharacteristic(LOCATION_INFO)) {
    err = "camera has no location service: turn on Bluetooth > Location Info. Link Set.";
    return false;
  }
  if (!setFlag(svc, LOCATION_ALLOW, 1) || !setFlag(svc, LOCATION_ENABLE, 1)) {
    err = "camera refused the location link: turn on Location Info. Link and Auto Time Correction";
    return false;
  }
  struct tm utc, local;
  gmtime_r(&now, &utc);
  localtime_r(&now, &local);
  int64_t localSecs = daysFromCivil(local.tm_year + 1900, local.tm_mon + 1, local.tm_mday) * 86400 +
                      local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec;
  int offsetMin = (int)((localSecs - (int64_t)now) / 60);        // includes summer time
  int dstMin = local.tm_isdst > 0 ? 60 : 0;

  uint8_t p[95] = {0x00, 0x5D, 0x08, 0x02, 0xFC, 0x03, 0x00, 0x00, 0x10, 0x10, 0x10};
  auto be32 = [&](int at, int32_t v) { p[at] = v >> 24; p[at + 1] = v >> 16; p[at + 2] = v >> 8; p[at + 3] = v; };
  auto be16 = [&](int at, int v) { p[at] = (v >> 8) & 0xFF; p[at + 1] = v & 0xFF; };
  be32(11, (int32_t)lround(STATION_LAT * 1e7));
  be32(15, (int32_t)lround(STATION_LON * 1e7));
  be16(19, utc.tm_year + 1900);                                 // the time is sent in UTC ...
  p[21] = utc.tm_mon + 1; p[22] = utc.tm_mday;
  p[23] = utc.tm_hour; p[24] = utc.tm_min; p[25] = utc.tm_sec;
  be16(91, offsetMin - dstMin);                                 // ... with the zone offset ...
  be16(93, dstMin);                                             // ... and the summer-time offset
  bool ok = svc->getCharacteristic(LOCATION_INFO)->writeValue(p, sizeof(p), true);
  Serial.printf("Sony: clock %04d-%02d-%02d %02d:%02d:%02d UTC, offset %d+%d min: %s\n", utc.tm_year + 1900,
                utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec, offsetMin - dstMin, dstMin, ok ? "ok" : "failed");
  if (STATION_LAT == 0 && STATION_LON == 0) {                   // no real location: do not tag photos with 0,0
    delay(500);                                                 // let the camera take the time first
    setFlag(svc, LOCATION_ENABLE, 0);
  }
  if (!ok) err = "camera did not take the time";
  return ok;
}

bool SonyRemote::holdC1(bool down) { return send(0x01, down ? 0x21 : 0x20); }   // C1 down 0x0121, up 0x0120

bool SonyRemote::toggleRecord() {   // same as pressing the MOVIE button
  bool ok = send(0x01, 0x0F);
  delay(150);
  return send(0x01, 0x0E) && ok;
}
