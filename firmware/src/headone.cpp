#include "headone.h"

#include "config.h"

bool HeadOne::isHeadOne(NimBLEAdvertisedDevice *dev) {
  if (!dev->haveName()) return false;
  const std::string &name = dev->getName();
  return name == ek::NAME_V1 || name == ek::NAME_V2;
}

void HeadOne::begin() {
  mutex_ = xSemaphoreCreateMutex();
  reply_ = xSemaphoreCreateBinary();
}

bool HeadOne::connected() { return client_ && client_->isConnected() && write_[DATA] && write_[IOCTL]; }

void HeadOne::disconnect() {
  if (client_ && client_->isConnected()) client_->disconnect();
}

uint16_t HeadOne::mtu() { return client_ && client_->isConnected() ? client_->getMTU() : 0; }

// One request; when expectType >= 0, waits for the reply of that type on the same channel.
bool HeadOne::exchange(Ch ch, const ek::Packet &p, int expectType, uint32_t timeoutMs) {
  if (!connected()) return false;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  xSemaphoreTake(reply_, 0);                       // drop a stale signal
  waitCh_ = expectType >= 0 ? ch : -1;
  waitType_ = expectType;
  bool ok = write_[ch]->writeValue(p.data, p.len, true);
  if (ok && expectType >= 0) ok = xSemaphoreTake(reply_, pdMS_TO_TICKS(timeoutMs)) == pdTRUE;
  waitCh_ = -1;
  waitType_ = -1;
  xSemaphoreGive(mutex_);
  return ok;
}

void HeadOne::onNotify(Ch ch, const uint8_t *d, size_t n) {
  if (!ek::checksumOk(d, n)) {               // usually a frame cut short by a small MTU
    badFrames_ = badFrames_ + 1;
    return;
  }
  if (ch == DATA) {
    ek::Status s;
    double pan;
    if (ek::parseStatus(d, n, s)) {
      status_ = s;
      battery_ = s.battery;
    } else if (ek::parsePanDeg(d, n, ek::CURRENT_POSITION, pan)) {
      pan_ = pan;
    }
  } else {
    uint16_t g;
    if (ek::parseGroupId(d, n, g)) group_ = g;
  }
  if (waitCh_ == ch && waitType_ == d[0]) xSemaphoreGive(reply_);
}

bool HeadOne::refreshStatus(ek::Status &out) {
  if (!exchange(DATA, ek::statusGet(), ek::R_GENERAL_STATUS, 800)) return false;
  out = status_;
  return true;
}

bool HeadOne::readPan(double &deg) {
  if (!exchange(DATA, ek::numericRead(ek::CURRENT_POSITION), ek::R_NUMERIC_READOUT, 800)) return false;
  deg = pan_;
  return true;
}

bool HeadOne::connect(const NimBLEAddress &address, const std::string &adv, String &err) {
  if (!client_) {
    client_ = NimBLEDevice::createClient();
    client_->setConnectTimeout(10);
  }
  write_[DATA] = write_[IOCTL] = read_[DATA] = read_[IOCTL] = nullptr;
  if (!client_->connect(address)) {
    err = "HeadONE found but would not connect (is the edelkrone app connected to it?)";
    return false;
  }

  // The SDK looks the four characteristics up across all services, by UUID.
  const NimBLEUUID dw(ek::DATA_WRITE), dr(ek::DATA_READ), iw(ek::IOCTL_WRITE), ir(ek::IOCTL_READ);
  for (NimBLERemoteService *svc : *client_->getServices(true)) {
    for (NimBLERemoteCharacteristic *c : *svc->getCharacteristics(true)) {
      const NimBLEUUID &u = c->getUUID();
      if (u == dw) write_[DATA] = c;
      else if (u == dr) read_[DATA] = c;
      else if (u == iw) write_[IOCTL] = c;
      else if (u == ir) read_[IOCTL] = c;
    }
  }
  if (!write_[DATA] || !read_[DATA] || !write_[IOCTL] || !read_[IOCTL]) {
    err = "HeadONE is missing its control characteristics; update its firmware with the edelkrone app";
    disconnect();
    return false;
  }
  bool subscribed =
      read_[DATA]->subscribe(true, [this](NimBLERemoteCharacteristic *, uint8_t *d, size_t n, bool) { onNotify(DATA, d, n); }, true) &&
      read_[IOCTL]->subscribe(true, [this](NimBLERemoteCharacteristic *, uint8_t *d, size_t n, bool) { onNotify(IOCTL, d, n); }, true);
  if (!subscribed) {
    err = "HeadONE refused notifications";
    disconnect();
    return false;
  }
  Serial.printf("HeadONE connected, MTU %u\n", mtu());

  ek::Status s{};
  bool gotStatus = false;
  for (int i = 0; i < 3 && !gotStatus; i++) {
    gotStatus = refreshStatus(s);
    if (!gotStatus) delay(300);
  }
  if (!gotStatus) {
    err = String("HeadONE did not answer the status request (MTU ") + mtu() + ", bad frames " + badFrames_ + ")";
    disconnect();
    return false;
  }

  // A lone head with no layout gets the same setup the SDK gives it: keep (or make) a group
  // id, then set the layout. A HeadONE upright on the turntable is a pan-only head.
  if (s.configuration == ek::SETUP_NONE) {
    const uint8_t *a = reinterpret_cast<const uint8_t *>(adv.data());
    if (ek::advTilted(a, adv.size())) {
      err = "HeadONE reports it is mounted tilted; mount it upright for the turntable";
      disconnect();
      return false;
    }
    exchange(IOCTL, ek::ioctlStatusGet(), ek::R_IOCTL_STATUS, 1000);
    uint16_t group = group_;
    if (group == ek::NO_GROUP && adv.size() >= 12) group = (uint16_t)((a[10] << 8) | a[11]);
    if (group == ek::NO_GROUP) group = (uint16_t)(esp_random() % 0xFFFE);
    exchange(IOCTL, ek::groupIdSelfSet(group), -1, 0);
    exchange(DATA, ek::configurationSet(ek::SETUP_PAN_ONLY), -1, 0);
    for (int i = 0; i < 10 && s.configuration == ek::SETUP_NONE; i++) {
      delay(300);
      refreshStatus(s);
    }
    Serial.printf("HeadONE layout set: %u (group 0x%04x)\n", s.configuration, group);
  }
  if (s.configuration == ek::SETUP_TILT_ONLY) {
    err = "HeadONE is set up as a tilt head; mount it upright and restart it";
    disconnect();
    return false;
  }
  double pan;
  if (!readPan(pan)) {
    err = "HeadONE gives no position reading; update its firmware with the edelkrone app";
    disconnect();
    return false;
  }
  Serial.printf("HeadONE ready: layout %u, pan %.2f, battery %d%%\n", s.configuration, pan, battery_);
  return true;
}

static float wrap180(double d) {
  while (d > 180) d -= 360;
  while (d < -180) d += 360;
  return (float)d;
}

bool HeadOne::turnBy(float tableDeg, float speed, volatile bool &abort, String &err) {
  const float target = tableDeg * HEAD_DEG_PER_TABLE_DEG;
  const float dir = TABLE_CLOCKWISE ? 1.0f : -1.0f;
  double last;
  if (!readPan(last)) {
    err = "no position reading before the turn";
    return false;
  }
  float travelled = 0;
  uint32_t lastProgress = millis(), lastRead = 0;
  bool ok = true;
  while (travelled < target - STOP_EARLY_DEG) {
    if (abort) { err = "stopped by request"; ok = false; break; }
    if (!connected()) { err = "HeadONE disconnected during the turn"; ok = false; break; }
    float remaining = target - travelled;
    float v = remaining > SLOW_ZONE_DEG ? speed : max(MIN_TURN_SPEED, speed * remaining / SLOW_ZONE_DEG);
    // The head answers each joystick command with a status frame; a missed answer is not fatal.
    exchange(DATA, ek::manualPanTilt(dir * v, 0), ek::R_GENERAL_STATUS, 400);
    if (millis() - lastRead >= 150) {        // the SDK reads the position every 150 ms while driving
      lastRead = millis();
      double now;
      if (readPan(now)) {
        float step = wrap180(now - last);
        last = now;
        if (fabsf(step) > 0.02f) lastProgress = millis();
        travelled += step * dir;
      }
    }
    if (millis() - lastProgress > STALL_TIMEOUT_MS) {
      err = "turntable is not moving (blocked, switched off or out of battery?)";
      ok = false;
      break;
    }
    delay(40);
  }
  stop();
  double end;
  if (readPan(end)) travelled += wrap180(end - last) * dir;
  Serial.printf("turned %.2f of %.2f head degrees\n", travelled, target);
  return ok;
}

// Same order as the SDK's move.stop(): stick to zero, abort any planned move, wait for idle.
void HeadOne::stop() {
  exchange(DATA, ek::manualPanTilt(0, 0), ek::R_GENERAL_STATUS, 400);
  exchange(DATA, ek::stopMove(), -1, 0);
  ek::Status s;
  for (uint32_t t0 = millis(); millis() - t0 < 2000;) {
    if (refreshStatus(s) && s.state != ek::ST_KEYPOSE_MOVING && s.state != ek::ST_NUMERIC_MOVE_RUNNING) break;
    delay(150);
  }
}

bool HeadOne::numericMove(float deg, String &report) {
  double before = NAN, after = NAN;
  readPan(before);
  if (!exchange(DATA, ek::numericPanMove(deg), -1, 0)) {
    report = "write failed";
    return false;
  }
  bool sawRunning = false;
  ek::Status s;
  uint32_t t0 = millis();
  while (millis() - t0 < 30000) {
    delay(250);
    if (!refreshStatus(s)) continue;
    if (s.state == ek::ST_NUMERIC_MOVE_RUNNING) sawRunning = true;
    else if (sawRunning || millis() - t0 > 2500) break;
  }
  readPan(after);
  auto num = [](double v) { return isnan(v) ? String("null") : String(v, 3); };
  report = String("{\"sent\":") + deg + ",\"before\":" + num(before) + ",\"after\":" + num(after) +
           ",\"head_reported_moving\":" + (sawRunning ? "true" : "false") + "}";
  return true;
}

bool HeadOne::fireShutter() { return exchange(DATA, ek::triggerShutter(), -1, 0); }
