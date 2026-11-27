// Auction station controller (ESP32-S3). Runs the whole capture on its own:
//   button -> open an item on the server -> 4 x (photo, turn 90 deg) -> film one full turn -> tell the server.
// Holds two Bluetooth links at once: the Sony camera and the edelkrone HeadONE turntable.
// The camera uploads the files to the server by itself over Wi-Fi (FTP).
//
// HTTP API (port 80), for testing from a browser or curl:
//   GET  /                        control page: status, pairing, capture, manual table control, settings
//   GET  /status                  everything at a glance (JSON)
//   POST /pair                    retry Bluetooth connects every 10 s for 90 s, forget a stale camera bond
//   POST /trigger                 run the capture (same as the button)
//   POST /session/start, /session/stop   open / close an item on the server: every run in between
//                                 goes into that one item (kits). Also: hold the station button 1 s.
//   POST /abort                   stop the running job and the table
//   GET  /settings                capture settings (JSON); POST /settings?stops=0,90,180,270&photo_speed=0.5 ...
//                                 changes any of them and keeps them in flash; POST /settings?reset=1 = config.h
//   POST /table/jog?deg=90&speed=0.5   turn by deg (negative = backwards)
//   POST /table/goto?deg=180      turn to an angle from the zero mark, the shortest way
//   POST /table/drive?speed=0.3   hold-to-move: repeat every ~300 ms while held; stops 0.7 s after the last one or at speed=0
//   POST /table/zero              call the current position 0 deg
//   POST /table/stop
//   POST /table/numeric?deg=90    experimental: head's own planned pan move, reports before/after angle
//   POST /table/shutter           fire the HeadONE's camera-port shutter
//   POST /camera/shutter, /camera/record
//   POST /camera/clock            set the camera's date and time from the station's NTP clock
//                                 (the server can ask for this too: check-in command "set_clock")
//   GET  /update                  firmware upload page (user admin, OTA_PASSWORD)
//   POST /update                  new firmware: curl -u admin:PASS -F firmware=@firmware.bin http://<esp>/update
// Also answers PlatformIO's espota uploads on port 3232 (pio run -e ota -t upload).

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <NimBLEDevice.h>
#include <Preferences.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>

#include "config.h"

#define FIRMWARE_VERSION "2026-11-27.8"
#include "headone.h"
#include "sony.h"

static WebServer web(80);
static HeadOne head;
static SonyRemote camera;

// ---- Bluetooth discovery and connecting ----
// The scan callback runs on the NimBLE host task; connecting (which blocks for seconds, longer when
// pairing fails) runs on bleTask, never in loop(), so the web server stays responsive.
// bleLock: held while connecting and for a whole job, so the two never use the links at once.
static SemaphoreHandle_t bleLock, noteLock;
static volatile bool headFound = false, cameraFound = false;
static NimBLEAddress headAddress, cameraAddress;
static std::string headAdv;
static String headError = "searching", cameraError = "searching";   // guarded by noteLock

// after a failed connect, wait 5 s, 10 s, 20 s ... up to 60 s before trying that device again
static const uint32_t RETRY_FIRST_MS = 5000, RETRY_MAX_MS = 60000;
static volatile uint32_t headRetryAt = 0, cameraRetryAt = 0;
static uint32_t headBackoff = 0, cameraBackoff = 0;
static bool due(uint32_t at) { return (int32_t)(millis() - at) >= 0; }

// "Start pairing" (web page or POST /pair): for PAIR_WINDOW_MS retry every PAIR_RETRY_MS, no backoff.
// Not faster: the camera refuses pairing attempts that come too close together ("Repeated Attempts").
static const uint32_t PAIR_WINDOW_MS = 90000, PAIR_RETRY_MS = 10000;
static volatile uint32_t pairUntil = 0;
static volatile bool scanNow = false, pairRequested = false;
static bool pairing() { return pairUntil && !due(pairUntil); }

static String getNote(const String &n) {
  xSemaphoreTake(noteLock, portMAX_DELAY);
  String copy = n;
  xSemaphoreGive(noteLock);
  return copy;
}
static void setNote(String &n, const String &v) {
  xSemaphoreTake(noteLock, portMAX_DELAY);
  n = v;
  xSemaphoreGive(noteLock);
}

// last devices heard in scans, for GET /devices (fixed buffers: filled on the NimBLE task)
struct Seen { char addr[18]; char name[24]; char mfr[33]; int8_t rssi; uint8_t kind; uint32_t at; };
static const int SEEN_MAX = 24;
static Seen seen[SEEN_MAX];
static portMUX_TYPE seenMux = portMUX_INITIALIZER_UNLOCKED;

static void remember(NimBLEAdvertisedDevice *dev, uint8_t kind) {   // kind: 0 other, 1 HeadONE, 2 Sony
  Seen s{};
  strlcpy(s.addr, dev->getAddress().toString().c_str(), sizeof(s.addr));
  if (dev->haveName()) strlcpy(s.name, dev->getName().c_str(), sizeof(s.name));
  if (dev->haveManufacturerData()) {
    std::string m = dev->getManufacturerData();
    for (size_t i = 0; i < m.size() && i < 16; i++) snprintf(s.mfr + i * 2, 3, "%02x", (uint8_t)m[i]);
  }
  s.rssi = dev->getRSSI(); s.kind = kind; s.at = millis();
  portENTER_CRITICAL(&seenMux);
  int slot = 0;
  for (int i = 0; i < SEEN_MAX; i++) {
    if (!strcmp(seen[i].addr, s.addr)) { slot = i; break; }
    if (seen[i].at < seen[slot].at) slot = i;              // otherwise replace the oldest
  }
  seen[slot] = s;
  portEXIT_CRITICAL(&seenMux);
}

class ScanCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice *dev) override {
    remember(dev, HeadOne::isHeadOne(dev) ? 1 : SonyRemote::isSonyCamera(dev) ? 2 : 0);
    if (!headFound && !head.connected() && due(headRetryAt) && HeadOne::isHeadOne(dev)) {
      headAddress = dev->getAddress();
      headAdv = dev->getServiceDataCount() ? dev->getServiceData((uint8_t)0) : std::string();
      headFound = true;
    } else if (!cameraFound && !camera.connected() && due(cameraRetryAt) && SonyRemote::isSonyCamera(dev) &&
               (!CAMERA_ADDRESS[0] || dev->getAddress().toString() == CAMERA_ADDRESS)) {
      cameraAddress = dev->getAddress();
      cameraFound = true;
    }
  }
};

// ---- capture settings: start from config.h, changed on the web page, kept in flash ----
static const int MAX_STOPS = 24;
struct Settings {
  uint16_t version;
  uint8_t nStops;
  float stops[MAX_STOPS];                                // photo angles, ascending, 0..<360
  float photoSpeed, videoSpeed;                          // fraction of the head's top speed
  uint8_t video, clockwise;
  uint16_t settleMs, waitMs, videoLeadMs;
  float headPerTable, slowZone, minSpeed, stopEarly;
};
static const uint16_t SETTINGS_VERSION = 1;
static Settings cfg;
static Preferences prefs;

// Hold the camera's C1 key while filming. With Recall Custom Hold on C1 the camera films with
// its saved settings (ISO, aperture, shutter) instead of the photo ones; the record button in
// photo mode otherwise always uses the photo settings. Kept apart from Settings so saved ones stay valid.
static bool videoC1 = false;
static const uint32_t VIDEO_C1_SETTLE_MS = 400;          // let the camera switch settings before recording
static bool testRecording = false;                       // the page's Start / stop video button

static void defaultSettings(Settings &s) {
  s = Settings{};
  s.version = SETTINGS_VERSION;
  s.nStops = PHOTOS;
  for (int i = 0; i < PHOTOS; i++) s.stops[i] = 360.0f * i / PHOTOS;
  s.photoSpeed = PHOTO_TURN_SPEED; s.videoSpeed = VIDEO_TURN_SPEED;
  s.video = RECORD_VIDEO; s.clockwise = TABLE_CLOCKWISE;
  s.settleMs = SETTLE_BEFORE_PHOTO_MS; s.waitMs = WAIT_AFTER_PHOTO_MS; s.videoLeadMs = VIDEO_LEAD_MS;
  s.headPerTable = HEAD_DEG_PER_TABLE_DEG; s.slowZone = SLOW_ZONE_DEG; s.minSpeed = MIN_TURN_SPEED; s.stopEarly = STOP_EARLY_DEG;
}

static void applySettings() {
  head.tuning.clockwise = cfg.clockwise;
  head.tuning.headPerTable = cfg.headPerTable;
  head.tuning.slowZone = cfg.slowZone;
  head.tuning.minSpeed = cfg.minSpeed;
  head.tuning.stopEarly = cfg.stopEarly;
}

static void loadSettings() {
  defaultSettings(cfg);
  prefs.begin("station", false);
  Settings s;
  if (prefs.getBytes("cfg", &s, sizeof(s)) == sizeof(s) && s.version == SETTINGS_VERSION) cfg = s;
  videoC1 = prefs.getBool("videoC1", false);
  if (prefs.isKey("zeroPan")) head.restoreZero(prefs.getDouble("zeroPan", 0));
  applySettings();
}

static bool needCamera() { return !SHUTTER_VIA_HEAD || cfg.video; }

// ---- jobs run on their own task so the web server and button stay responsive ----
enum JobKind { JOB_CAPTURE, JOB_JOG, JOB_NUMERIC, JOB_DRIVE, JOB_GOTO };
static const char *JOB_NAME[] = {"capture", "turn", "numeric", "drive", "goto"};
struct Job { JobKind kind; float deg; float speed; };
static QueueHandle_t jobs;
static volatile bool busy = false, abortRequested = false;
static volatile JobKind currentJob = JOB_CAPTURE;
static volatile bool updating = false;                   // firmware update running: no Bluetooth work
static String lastResult = "idle";
static int lastItem = -1;

// hold-to-move: the page repeats /table/drive while a button is held; the job lets go when that stops
static const uint32_t DRIVE_HOLD_MS = 700;
static volatile float driveSpeed = 0;
static volatile uint32_t driveUntil = 0;
// Each hold has an id from the page. Requests can arrive late or out of order, so once a hold is
// released its late repeats are ignored instead of starting the table again.
static uint32_t releasedHolds[8];
static int releasedAt = 0;
static bool holdReleased(uint32_t id) {
  if (!id) return false;
  for (uint32_t r : releasedHolds) if (r == id) return true;
  return false;
}

static bool startJob(JobKind kind, float deg = 0, float speed = PHOTO_TURN_SPEED) {
  if (busy) return false;
  Job j{kind, deg, speed};
  return xQueueSend(jobs, &j, 0) == pdTRUE;
}

// ---- server ----
static int serverPost(const String &path, const String &body, String &response) {
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(String(SERVER_URL) + path);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Station-Key", STATION_KEY);
  int code = http.POST(body);
  response = code > 0 ? http.getString() : http.errorToString(code);
  http.end();
  return code;
}

// A new item, or, while an item is open on the server (Start item / a kit), that item's id.
// Returns -1 when the server is unreachable: the files then become loose shots.
static int openItem(const Settings &s) {
  String resp;
  int code = serverPost("/api/items", String("{\"photos\":") + s.nStops + ",\"videos\":" + (s.video ? 1 : 0) + "}", resp);
  int at = resp.indexOf("\"id\"");
  if (code != 200 || at < 0) {
    Serial.printf("Server did not open an item (%d %s); the photos will become a manual item\n", code, resp.c_str());
    return -1;
  }
  return resp.substring(resp.indexOf(':', at) + 1).toInt();
}

// ---- the capture ----
static bool shoot() { return SHUTTER_VIA_HEAD ? head.fireShutter() : camera.takePhoto(); }

// Turns to an angle from the zero mark, using the measured angle (so small errors do not add up).
// forward: keep the capture direction (except a tiny step back after an overshoot); else shortest way.
static bool turnTo(float target, float speed, bool forward, String &err) {
  double p;
  if (!head.readPan(p)) { err = "no position reading from the turntable"; return false; }
  float delta = fmodf(target - head.angle() + 720.0f, 360.0f);   // 0..360 forward
  if (delta > 355.0f || (!forward && delta > 180.0f)) delta -= 360.0f;
  if (fabsf(delta) < 0.2f) return true;
  return head.turnBy(delta, speed, abortRequested, err);
}

// Photos at each stop angle (measured from the zero mark), then back to 0, then optionally one
// filmed full turn starting and ending at 0.
static bool capture(String &err) {
  const Settings s = cfg;                       // settings changed mid-capture apply next time
  if (!head.connected()) { err = "turntable not connected: " + getNote(headError); return false; }
  if ((!SHUTTER_VIA_HEAD || s.video) && !camera.connected()) { err = "camera not connected: " + getNote(cameraError); return false; }
  lastItem = openItem(s);
  Serial.printf("Capture started, item %d, %u photos\n", lastItem, s.nStops);
  for (int n = 0; n < s.nStops; n++) {
    // the first stop the shortest way from wherever the table is; the rest onwards in the capture direction
    if (!turnTo(s.stops[n], s.photoSpeed, n > 0, err)) return false;
    delay(s.settleMs);
    if (abortRequested) { err = "stopped by request"; return false; }
    if (!shoot()) { err = String("photo ") + (n + 1) + " failed"; return false; }
    delay(s.waitMs);
  }
  if (s.video) {
    if (!turnTo(0, s.photoSpeed, true, err)) return false;   // film from 0, whatever the last stop was
    Serial.printf("At %.1f deg before filming\n", head.angle());
    delay(s.settleMs);
    if (videoC1) { camera.holdC1(true); delay(VIDEO_C1_SETTLE_MS); }   // video exposure via Recall Custom Hold
    if (!camera.toggleRecord()) { if (videoC1) camera.holdC1(false); err = "could not start the video"; return false; }
    delay(s.videoLeadMs);
    bool turned = head.turnBy(360.0f, s.videoSpeed, abortRequested, err);
    delay(s.videoLeadMs);
    camera.toggleRecord();                     // stop filming even if the turn failed
    if (videoC1) { delay(300); camera.holdC1(false); }
    if (!turned) return false;
  } else if (!turnTo(0, s.photoSpeed, true, err)) {      // ready for the next item
    return false;
  }
  if (lastItem > 0) {
    String resp;
    serverPost(String("/api/items/") + lastItem + "/captured", "", resp);
  }
  return true;
}

static void jobTask(void *) {
  Job j;
  for (;;) {
    xQueueReceive(jobs, &j, portMAX_DELAY);
    currentJob = j.kind;
    busy = true;
    xSemaphoreTake(bleLock, portMAX_DELAY);              // waits for a connect in progress to finish
    abortRequested = false;
    String err, report;
    bool ok = false;
    switch (j.kind) {
      case JOB_CAPTURE: ok = capture(err); if (ok) report = String("item ") + lastItem + " captured"; break;
      case JOB_JOG: ok = head.turnBy(j.deg, j.speed, abortRequested, err); if (ok) report = String("turned ") + String(j.deg, 1) + " deg"; break;
      case JOB_NUMERIC: ok = head.numericMove(j.deg, report); break;
      case JOB_GOTO: {
        double p;
        if (!head.readPan(p)) { err = "turntable not connected"; break; }   // fresh angle
        float delta = fmodf(j.deg - head.angle() + 540.0f, 360.0f) - 180.0f;   // shortest way round
        ok = fabsf(delta) < 0.2f || head.turnBy(delta, j.speed, abortRequested, err);
        if (ok) report = String("at ") + String(head.angle(), 1) + " deg";
        break;
      }
      case JOB_DRIVE: {
        uint32_t lastRead = 0;
        ok = head.connected();
        if (!ok) err = "turntable not connected";
        while (ok && !abortRequested && !due(driveUntil)) {
          head.drive(driveSpeed);
          if (abortRequested || due(driveUntil)) break;  // let go while that command was answered
          if (millis() - lastRead >= 300) { double p; head.readPan(p); lastRead = millis(); }   // keep the angle followed
          delay(40);
        }
        head.stop();
        double p;
        head.readPan(p);
        if (ok) report = String("moved by hand to ") + String(head.angle(), 1) + " deg";
        break;
      }
    }
    if (!ok) head.stop();
    lastResult = ok ? report : "error: " + err;
    Serial.println(lastResult);
    xSemaphoreGive(bleLock);
    busy = false;
  }
}

// ---- Bluetooth task: scan for whatever is missing, connect when found, back off after failures ----
static void connectDevice(bool isHead) {
  String err;
  bool ok = isHead ? head.connect(headAddress, headAdv, err) : camera.connect(cameraAddress, err);
  uint32_t &backoff = isHead ? headBackoff : cameraBackoff;
  if (ok) {
    backoff = 0;
    (isHead ? headRetryAt : cameraRetryAt) = millis();
    setNote(isHead ? headError : cameraError, "ok");
    return;
  }
  if (!isHead && err.indexOf("too many pairing attempts") >= 0) backoff = RETRY_MAX_MS;   // let the camera cool down
  else if (pairing()) backoff = PAIR_RETRY_MS;
  else backoff = backoff ? min(backoff * 2, RETRY_MAX_MS) : RETRY_FIRST_MS;
  (isHead ? headRetryAt : cameraRetryAt) = millis() + backoff;
  String note = err + " (retrying in " + (backoff / 1000) + " s)";
  setNote(isHead ? headError : cameraError, note);
  Serial.println(note);
}

static void beginPairing();

static void bleTask(void *) {
  uint32_t lastScan = 0, lastPanRead = 0, lastStatus = 0;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(50));
    if (updating || busy) continue;
    NimBLEScan *scan = NimBLEDevice::getScan();
    // keep the table angle (someone may turn it by hand) and battery fresh for the page
    if (head.connected() && millis() - lastPanRead > 700 && xSemaphoreTake(bleLock, 0) == pdTRUE) {
      double p;
      head.readPan(p);
      if (millis() - lastStatus > 15000) { ek::Status st; head.refreshStatus(st); lastStatus = millis(); }
      lastPanRead = millis();
      xSemaphoreGive(bleLock);
    }
    if (pairRequested && xSemaphoreTake(bleLock, 0) == pdTRUE) {
      beginPairing();
      xSemaphoreGive(bleLock);
    }
    if (headFound || cameraFound) {
      if (scan->isScanning()) { scan->stop(); continue; }
      if (xSemaphoreTake(bleLock, 0) != pdTRUE) continue;   // a job just started; try after it
      if (!updating && headFound) connectDevice(true);
      if (!updating && cameraFound) connectDevice(false);
      headFound = cameraFound = false;
      xSemaphoreGive(bleLock);
      continue;
    }
    bool wantHead = !head.connected() && due(headRetryAt);
    bool wantCamera = needCamera() && !camera.connected() && due(cameraRetryAt);
    if ((wantHead || wantCamera) && !scan->isScanning() && (scanNow || millis() - lastScan > 3000)) {
      lastScan = millis();
      scanNow = false;
      scan->start(5, nullptr, false);                    // 5 s, non-blocking
    }
  }
}

// direct camera/table commands from the web: only when no connect or job is using the links
static int withLinks(bool (*action)()) {
  if (xSemaphoreTake(bleLock, pdMS_TO_TICKS(200)) != pdTRUE) return 409;
  bool ok = action();
  xSemaphoreGive(bleLock);
  return ok ? 200 : 503;
}

// ---- camera clock: the station keeps NTP time; this sends it to the camera ----
static String clockNote = "not set since the station started";   // guarded by noteLock
static bool haveTime() { return time(nullptr) > 1700000000; }      // NTP has answered
static String localTimeText(time_t t) {
  struct tm lt;
  localtime_r(&t, &lt);
  char buf[24];
  strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &lt);
  return buf;
}

static int setCameraClock(String &msg) {
  if (!haveTime()) { msg = "the station has no time from " NTP_SERVER " yet"; return 503; }
  if (busy || xSemaphoreTake(bleLock, pdMS_TO_TICKS(2000)) != pdTRUE) { msg = "busy, try again in a moment"; return 409; }
  String err;
  time_t now = time(nullptr);
  bool ok = camera.setClock(now, err);
  xSemaphoreGive(bleLock);
  msg = ok ? "set to " + localTimeText(now) : err;
  setNote(clockNote, ok ? msg : "failed: " + err);
  return ok ? 200 : 503;
}

// Open the pairing window: retry fast, scan right away, and drop a stale camera bond
// (a camera that was reset or paired elsewhere refuses the old keys, so pairing never finishes).
// Runs on bleTask between connect attempts; the web handler only sets pairRequested so it answers at once.
static void beginPairing() {
  pairRequested = false;
  if (!camera.connected()) NimBLEDevice::deleteAllBonds();   // only the camera bonds; the HeadONE does not
  headBackoff = cameraBackoff = 0;
  headRetryAt = cameraRetryAt = millis();
  if (!head.connected()) setNote(headError, "pairing: searching");
  if (!camera.connected()) setNote(cameraError, "pairing: open Bluetooth > Pairing on the camera");
  scanNow = true;
}

static int startPairing() {
  if (busy) return 409;
  pairUntil = millis() + PAIR_WINDOW_MS;                 // shows in /status right away
  pairRequested = true;
  return 202;
}

// ---- status LED: red nothing, yellow one device, green ready, blue busy, flashing red error ----
static void led(uint8_t r, uint8_t g, uint8_t b) {
  if (LED_RGB_PIN >= 0) neopixelWrite(LED_RGB_PIN, r, g, b);
}

static void updateLed() {
  bool cam = camera.connected() || !needCamera(), table = head.connected();
  if (busy) led(0, 0, 40);
  else if (lastResult.startsWith("error") && (millis() / 300) % 2) led(60, 0, 0);
  else if (cam && table) led(0, 40, 0);
  else if (cam || table) led(40, 25, 0);
  else led(40, 0, 0);
}

// ---- web ----
static void reply(int code, const String &json) { web.send(code, "application/json", json); }
static String quote(const String &s) { String q = s; q.replace("\\", "\\\\"); q.replace("\"", "\\\""); return "\"" + q + "\""; }

static float argSpeed(float fallback) {                  // ?speed= as a fraction 0.02..1
  return web.hasArg("speed") ? constrain(web.arg("speed").toFloat(), 0.02f, 1.0f) : fallback;
}

static String stopsText(const Settings &s) {
  String t;
  for (int i = 0; i < s.nStops; i++) {
    String v = String(s.stops[i], s.stops[i] == (int)s.stops[i] ? 0 : 1);
    v.trim();                                            // String(float) pads to a minimum width
    if (i) t += ",";
    t += v;
  }
  return t;
}

static String settingsJson() {
  const Settings &s = cfg;
  String j = "{\"stops\":\"" + stopsText(s) + "\"";
  j += ",\"photo_speed\":" + String(s.photoSpeed, 3) + ",\"video_speed\":" + String(s.videoSpeed, 3);
  j += ",\"video\":" + String(s.video) + ",\"clockwise\":" + String(s.clockwise);
  j += ",\"settle_ms\":" + String(s.settleMs) + ",\"wait_ms\":" + String(s.waitMs) + ",\"video_lead_ms\":" + String(s.videoLeadMs);
  j += ",\"head_per_table\":" + String(s.headPerTable, 4) + ",\"slow_zone\":" + String(s.slowZone, 2);
  j += ",\"min_speed\":" + String(s.minSpeed, 3) + ",\"stop_early\":" + String(s.stopEarly, 2);
  j += ",\"video_c1\":" + String(videoC1 ? 1 : 0) + "}";
  return j;
}

// Reads the fields present in the request into s; false with err when a value is out of range.
static bool settingsFromArgs(Settings &s, String &err) {
  auto num = [&](const char *name, float lo, float hi, float &out) {
    if (!web.hasArg(name)) return true;
    float v = web.arg(name).toFloat();
    if (v < lo || v > hi) { err = String(name) + " must be " + String(lo, 2) + " to " + String(hi, 2); return false; }
    out = v;
    return true;
  };
  if (web.hasArg("stops")) {                             // "0,90,180,270": photo angles from the start
    float list[MAX_STOPS];
    int n = 0;
    String t = web.arg("stops") + ",";
    for (int from = 0, at; (at = t.indexOf(',', from)) >= 0; from = at + 1) {
      String part = t.substring(from, at);
      part.trim();
      if (part.isEmpty()) continue;
      if (n == MAX_STOPS) { err = String("at most ") + MAX_STOPS + " stops"; return false; }
      float v = part.toFloat();
      if (v < 0 || v >= 360) { err = "stop angles must be 0 to 359.9"; return false; }
      list[n++] = v;
    }
    if (!n) { err = "give at least one stop angle"; return false; }
    std::sort(list, list + n);
    s.nStops = n;
    memcpy(s.stops, list, n * sizeof(float));
  }
  float f, ms;
  bool ok = num("photo_speed", 0.02f, 1, s.photoSpeed) && num("video_speed", 0.02f, 1, s.videoSpeed) &&
            num("head_per_table", 0.05f, 50, s.headPerTable) && num("slow_zone", 0, 90, s.slowZone) &&
            num("min_speed", 0.01f, 0.5f, s.minSpeed) && num("stop_early", 0, 10, s.stopEarly);
  if (!ok) return false;
  f = s.video;      if (!num("video", 0, 1, f)) return false;      s.video = f >= 0.5f;
  f = s.clockwise;  if (!num("clockwise", 0, 1, f)) return false;  s.clockwise = f >= 0.5f;
  ms = s.settleMs;    if (!num("settle_ms", 0, 10000, ms)) return false;     s.settleMs = ms;
  ms = s.waitMs;      if (!num("wait_ms", 0, 30000, ms)) return false;       s.waitMs = ms;
  ms = s.videoLeadMs; if (!num("video_lead_ms", 0, 10000, ms)) return false; s.videoLeadMs = ms;
  return true;
}

// ---- over-the-air updates ----
static String updateError;

static void prepareForUpdate() {
  updating = true;
  abortRequested = true;                                 // stop a running capture and the table
  for (int i = 0; i < 50 && busy; i++) delay(100);
  if (xSemaphoreTake(bleLock, pdMS_TO_TICKS(10000)) == pdTRUE) xSemaphoreGive(bleLock);  // let a connect finish
  head.stop();
  NimBLEDevice::getScan()->stop();
  led(40, 0, 40);
}

static const char UPDATE_PAGE[] PROGMEM = R"html(<!doctype html><meta name="viewport" content="width=device-width">
<title>Auction station update</title><body style="font:16px system-ui;max-width:480px;margin:40px auto">
<h2>Firmware update</h2><p>Choose <code>firmware.bin</code> (from <code>.pio/build/esp32s3/</code>).</p>
<form method="POST" action="/update" enctype="multipart/form-data">
<input type="file" name="firmware" accept=".bin" required> <button>Upload</button></form>
<p style="color:#666">The device restarts when the upload finishes.</p></body>)html";

static void setupOtaWeb() {
  web.on("/update", HTTP_GET, [] {
    if (!web.authenticate("admin", OTA_PASSWORD)) return web.requestAuthentication();
    web.send_P(200, "text/html", UPDATE_PAGE);
  });
  web.on(
      "/update", HTTP_POST,
      [] {                                               // runs after the upload has finished
        if (!web.authenticate("admin", OTA_PASSWORD)) return web.requestAuthentication();
        bool ok = !Update.hasError() && updateError.isEmpty();
        reply(ok ? 200 : 500, ok ? "{\"ok\":true,\"restarting\":true}" : "{\"ok\":false,\"error\":" + quote(updateError) + "}");
        if (ok) { delay(500); ESP.restart(); }
        updating = false;
      },
      [] {                                               // runs for each chunk of the upload
        HTTPUpload &up = web.upload();
        if (up.status == UPLOAD_FILE_START) {
          updateError = "";
          if (!web.authenticate("admin", OTA_PASSWORD)) { updateError = "not authorised"; return; }
          prepareForUpdate();
          Serial.printf("OTA upload: %s\n", up.filename.c_str());
          if (!Update.begin(UPDATE_SIZE_UNKNOWN)) updateError = Update.errorString();
        } else if (up.status == UPLOAD_FILE_WRITE && updateError.isEmpty()) {
          if (Update.write(up.buf, up.currentSize) != up.currentSize) updateError = Update.errorString();
        } else if (up.status == UPLOAD_FILE_END && updateError.isEmpty()) {
          if (!Update.end(true)) updateError = Update.errorString();
          else Serial.printf("OTA upload done, %u bytes\n", up.totalSize);
        } else if (up.status == UPLOAD_FILE_ABORTED) {
          Update.abort();
          updateError = "upload aborted";
        }
      });
}

static void setupArduinoOta() {
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.setMdnsEnabled(false);                      // MDNS is started below for both
  ArduinoOTA.onStart(prepareForUpdate);
  ArduinoOTA.onEnd([] { Serial.println("OTA done, restarting"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("OTA error %u\n", e); updating = false; });
  ArduinoOTA.begin();
  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    MDNS.enableArduino(3232, true);
  }
}

// ---- an item open on the server (Start item / Stop item): every run until Stop goes into it ----
static volatile int sessionItem = 0;                     // 0 = no item open
static volatile int sessionPhotos = 0, sessionVideos = 0;
static volatile uint32_t sessionSeconds = 0;
static volatile int sessionWanted = 0;                   // 1 start, 2 stop: sent by pollTask
static String sessionNote = "";                          // guarded by noteLock

static String statusJson() {
  String j = "{";
  j += "\"version\":" + quote(FIRMWARE_VERSION);
  j += ",\"busy\":" + String(busy ? "true" : "false");
  j += ",\"job\":" + String(busy ? quote(JOB_NAME[currentJob]) : "null");
  j += ",\"last\":" + quote(lastResult);
  j += ",\"last_item\":" + String(lastItem);
  j += ",\"camera\":" + String(camera.connected() ? "true" : "false") + ",\"camera_note\":" + quote(getNote(cameraError));
  j += ",\"table\":" + String(head.connected() ? "true" : "false") + ",\"table_note\":" + quote(getNote(headError));
  j += ",\"table_angle\":" + String(head.angle(), 1);
  j += ",\"table_pan_deg\":" + String(head.lastPan(), 2) + ",\"table_battery\":" + String(head.battery());
  j += ",\"table_mtu\":" + String(head.mtu()) + ",\"table_bad_frames\":" + String(head.badFrames());
  j += ",\"pairing_s\":" + String(pairing() ? (pairUntil - millis() + 999) / 1000 : 0);
  j += ",\"time\":" + String(haveTime() ? quote(localTimeText(time(nullptr))) : "null");
  j += ",\"camera_clock\":" + quote(getNote(clockNote));
  j += ",\"session_item\":" + String(sessionItem) + ",\"session_photos\":" + String(sessionPhotos);
  j += ",\"session_videos\":" + String(sessionVideos) + ",\"session_seconds\":" + String(sessionSeconds);
  j += ",\"session_note\":" + quote(getNote(sessionNote)) + ",\"session_pending\":" + String(sessionWanted);
  j += ",\"wifi_rssi\":" + String(WiFi.RSSI()) + "}";
  return j;
}

// Checks in with the server about once a second: sends the status, takes a waiting
// command ("capture" or "abort") from the reply. Lets the site start and stop runs.
// On its own task: a slow or unreachable server must not stall the local page or hold-to-turn.
static int serverCall(const char *method, const String &path, const String &body, String &resp) {
  HTTPClient http;
  http.setConnectTimeout(800);
  http.setTimeout(1500);
  http.begin(String(SERVER_URL) + path);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Station-Key", STATION_KEY);
  int code = http.sendRequest(method, body);
  resp = code > 0 ? http.getString() : http.errorToString(code);
  http.end();
  return code;
}

// Reads "session": {item_id, seconds, photos, videos} | null from a server reply; false if absent.
static bool readSession(const String &r) {
  int at = r.indexOf("\"session\":");
  if (at < 0) return false;
  String rest = r.substring(at + 10);
  rest.trim();
  if (rest.startsWith("null")) { sessionItem = 0; return true; }
  auto field = [&](const char *name) {
    int f = rest.indexOf(String("\"") + name + "\":");
    return f < 0 ? 0.0f : rest.substring(f + strlen(name) + 3).toFloat();
  };
  sessionItem = (int)field("item_id");
  sessionSeconds = (uint32_t)field("seconds");
  sessionPhotos = (int)field("photos");
  sessionVideos = (int)field("videos");
  return true;
}

static void doSessionRequest() {
  int want = sessionWanted;
  sessionWanted = 0;
  String resp;
  int code = serverCall("POST", want == 1 ? "/api/session/start" : "/api/session/stop", "", resp);
  if (code == 200) {
    readSession(resp);
    setNote(sessionNote, want == 1 ? String("item #") + sessionItem + " open" : "item closed");
  } else {
    setNote(sessionNote, code == 409 ? String("no item was open") : String("server said ") + code);
  }
  Serial.println("Session: " + getNote(sessionNote));
}

// Checks in with the server about once a second: sends the status, takes a waiting
// command ("capture" or "abort") from the reply. Lets the site start and stop runs.
// Also follows the item the site may have open (Start item / Stop item), and sends this
// station's own Start / Stop item requests.
// On its own task: a slow or unreachable server must not stall the local page or hold-to-turn.
static void pollTask(void *) {
  uint32_t lastSessionRead = 0;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(sessionWanted ? 50 : 1000));
    if (updating || WiFi.status() != WL_CONNECTED) continue;
    if (sessionWanted) { doSessionRequest(); continue; }
    String r;
    if (serverCall("POST", "/api/station/poll", statusJson(), r) != 200) continue;
    if (r.indexOf("\"command\":\"capture\"") >= 0) {
      if (!busy) startJob(JOB_CAPTURE);
    } else if (r.indexOf("\"command\":\"abort\"") >= 0) {
      abortRequested = true;
    } else if (r.indexOf("\"command\":\"set_clock\"") >= 0) {   // if the server offers it
      String msg;
      setCameraClock(msg);
    }
    // The poll reply may carry the open item; if it does not, ask GET /api/station now and then.
    if (!readSession(r) && millis() - lastSessionRead > 3000) {
      String s;
      if (serverCall("GET", "/api/station", "", s) == 200) readSession(s);
      lastSessionRead = millis();
    }
  }
}

// ---- control page at / : polls /status, buttons call /pair, /trigger, /abort ----
static const char INDEX_PAGE[] PROGMEM = R"html(<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Auction station</title>
<style>
:root{color-scheme:light dark;--bg:#f4f4f2;--card:#fff;--fg:#1c1c1c;--mute:#6b6b6b;--line:#e2e2de;
--ok:#1f8a4c;--warn:#b7791f;--bad:#c53030;--acc:#2b6cb0}
@media(prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--fg:#ececec;--mute:#9a9a9a;--line:#2e2e2e;
--ok:#48bb78;--warn:#ecc94b;--bad:#fc8181;--acc:#63b3ed}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,sans-serif}
main{max-width:520px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--mute);font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.row{display:flex;align-items:center;gap:10px}.name{font-weight:600;flex:1}
.dot{width:12px;height:12px;border-radius:50%;background:var(--mute);flex:none}
.ok .dot{background:var(--ok)}.wait .dot{background:var(--warn)}.bad .dot{background:var(--bad)}
.state{font-size:14px;color:var(--mute)}.ok .state{color:var(--ok)}
.note{color:var(--mute);font-size:14px;margin:6px 0 0 22px}.note:empty{display:none}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:14px}.kv dt{color:var(--mute)}.kv dd{margin:0}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0 12px}
button{font:inherit;font-weight:600;padding:14px;border-radius:10px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}button:disabled{opacity:.45;cursor:default}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button.stop{background:var(--bad);border-color:var(--bad);color:#fff;grid-column:1/-1}
#msg{font-size:14px;min-height:20px;color:var(--mute)}#msg.err{color:var(--bad)}
.hint{font-size:13px;color:var(--mute)}footer{font-size:12px;color:var(--mute);margin-top:18px}
footer a{color:inherit}
h2{font-size:15px;margin:0 0 12px}details>summary{cursor:pointer;font-weight:600;font-size:15px}
details[open]>summary{margin-bottom:12px}
.angle{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.angle b{font-size:34px;font-variant-numeric:tabular-nums}.angle span{color:var(--mute);font-size:13px;flex:1}
.small{padding:8px 12px;font-size:14px;font-weight:500}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.hold{font-size:22px;padding:18px;touch-action:none;user-select:none;-webkit-user-select:none}
.hold.on{background:var(--acc);border-color:var(--acc);color:#fff}
.field{display:grid;grid-template-columns:1fr auto;align-items:center;gap:6px 12px;margin:10px 0;font-size:14px}
.field label{color:var(--mute)}.field .wide{grid-column:1/-1}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
input[type=number]{width:96px}input[type=range]{width:100%;padding:0}input[type=checkbox]{width:20px;height:20px}
.inline{display:flex;gap:8px;align-items:center}.inline input[type=text],.inline input:not([type]){flex:1;min-width:0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chips button{padding:6px 10px;font-size:13px;font-weight:500}
.sep{border:0;border-top:1px solid var(--line);margin:14px 0}
</style>
<main>
<h1>Auction station</h1><div class="sub" id="conn">connecting…</div>
<div class="card" id="cam"><div class="row"><span class="dot"></span><span class="name">Camera</span><span class="state"></span></div><div class="note"></div></div>
<div class="card" id="tab"><div class="row"><span class="dot"></span><span class="name">Turntable</span><span class="state"></span></div><div class="note"></div></div>
<div class="btns">
<button id="pair">Start pairing</button>
<button id="cap" class="primary">Start capture</button>
<button id="sess" style="grid-column:1/-1" title="For a kit: every capture until Stop item goes into one listing">Start item (kit)</button>
<div id="sessinfo" class="hint" style="grid-column:1/-1;margin-top:-4px" hidden></div>
<button id="stop" class="stop" hidden>Stop</button>
</div>
<div id="msg"></div>
<p class="hint" id="pairhint" hidden>Pairing is open. On the camera: <b>Bluetooth Rmt Ctrl</b> on, then <b>Bluetooth &gt; Pairing</b>. Turn the HeadONE on and close the edelkrone app.</p>

<div class="card" id="manual"><h2>Turntable control</h2>
<div class="angle"><b id="ang">–</b><span>from the zero mark</span><button class="small" id="zero">Set zero here</button></div>
<div class="field"><label for="spd">Speed</label><span id="spdv"></span>
<input class="wide" type="range" id="spd" min="2" max="100" value="30"></div>
<div class="grid2"><button class="hold" data-dir="-1" title="Hold (or the left arrow key)">◀ Hold</button>
<button class="hold" data-dir="1" title="Hold (or the right arrow key)">Hold ▶</button></div>
<div class="grid4" style="margin-top:10px">
<button class="small jog" data-deg="-90">−90°</button><button class="small jog" data-deg="-45">−45°</button>
<button class="small jog" data-deg="-10">−10°</button><button class="small jog" data-deg="-1">−1°</button>
<button class="small jog" data-deg="1">+1°</button><button class="small jog" data-deg="10">+10°</button>
<button class="small jog" data-deg="45">+45°</button><button class="small jog" data-deg="90">+90°</button></div>
<hr class="sep"><div class="inline"><label for="to">Go to</label><input type="number" id="to" min="0" max="359.9" step="0.1" value="0">°
<button class="small" id="go">Go</button><button class="small" id="tstop">Stop</button></div>
<div class="chips" id="stopchips"></div></div>

<div class="card"><details id="setdet"><summary>Capture settings</summary>
<div class="field"><label class="wide" for="stops">Photo stops (degrees from the start, comma separated)</label>
<input class="wide" id="stops" placeholder="0,90,180,270"></div>
<div class="inline"><label for="even">Spread</label><input type="number" id="even" min="1" max="24" value="4">
<button class="small" id="fill">photos evenly</button></div>
<div class="field"><label for="pspd">Turn speed between photos</label><span id="pspdv"></span>
<input class="wide" type="range" id="pspd" min="2" max="100"></div>
<div class="field"><label for="settle">Settle before each photo (ms)</label><input type="number" id="settle" min="0" max="10000" step="50"></div>
<div class="field"><label for="wait">Wait after each photo (ms)</label><input type="number" id="wait" min="0" max="30000" step="50"></div>
<div class="field"><label for="video">Film one full turn after the photos</label><input type="checkbox" id="video"></div>
<div class="field"><label for="vc1">Hold C1 while filming (video exposure from Recall Custom Hold)</label><input type="checkbox" id="vc1"></div>
<div class="field"><label for="vspd">Turn speed while filming</label><span id="vspdv"></span>
<input class="wide" type="range" id="vspd" min="2" max="100"></div>
<div class="field"><label for="dir">Direction</label><select id="dir"><option value="1">Clockwise</option><option value="0">Counter-clockwise</option></select></div>
<details><summary style="font-size:14px">Fine tuning</summary>
<div class="field"><label for="hpt">Head degrees per table degree</label><input type="number" id="hpt" min="0.05" max="50" step="0.001"></div>
<div class="field"><label for="slow">Slow down this many degrees before a stop</label><input type="number" id="slow" min="0" max="90" step="0.5"></div>
<div class="field"><label for="minspd">Slowest speed (%)</label><input type="number" id="minspd" min="1" max="50" step="1"></div>
<div class="field"><label for="early">Stop early by (degrees; raise if it overshoots)</label><input type="number" id="early" min="0" max="10" step="0.1"></div>
<div class="field"><label for="lead">Still video before and after the spin (ms)</label><input type="number" id="lead" min="0" max="10000" step="100"></div>
</details>
<div class="grid2" style="margin-top:14px"><button class="primary" id="save">Save settings</button><button id="reset">Back to defaults</button></div>
<div id="setmsg" class="hint" style="margin-top:8px"></div></details></div>

<div class="card"><h2>Camera test</h2><div class="grid2">
<button class="small" id="shot">Take a photo</button><button class="small" id="rec">Start / stop video</button></div>
<hr class="sep"><dl class="kv"><dt>Station time</dt><dd id="now">–</dd><dt>Camera clock</dt><dd id="clk">–</dd></dl>
<button class="small" id="setclk" style="margin-top:10px;width:100%">Set the camera's date and time</button></div>

<div class="card"><dl class="kv">
<dt>Last result</dt><dd id="last">–</dd><dt>Last item</dt><dd id="item">–</dd>
<dt>Table battery</dt><dd id="bat">–</dd><dt>Wi-Fi</dt><dd id="rssi">–</dd></dl></div>
<footer><a href="/status">/status</a> · <a href="/settings">/settings</a> · <a href="/update">firmware update</a></footer>
</main>
<script>
const $=id=>document.getElementById(id);let s=null,fails=0;
function dev(el,on,note,pairing){el.className='card '+(on?'ok':pairing||/search/.test(note)?'wait':'bad');
el.querySelector('.state').textContent=on?'connected':pairing?'pairing…':'not connected';
el.querySelector('.note').textContent=on?'':note;}
function show(){if(!s)return;const p=s.pairing_s>0;
dev($('cam'),s.camera,s.camera_note,p);dev($('tab'),s.table,s.table_note,p);
$('last').textContent=s.last;$('item').textContent=s.last_item>0?'#'+s.last_item:'–';
$('bat').textContent=s.table_battery>=0?s.table_battery+' %':'–';
$('rssi').textContent=s.wifi_rssi+' dBm'+(s.wifi_rssi<-75?' (weak)':'');
$('pair').textContent=p?'Pairing… '+s.pairing_s+' s':'Start pairing';$('pair').disabled=s.busy||p;
const linked=s.camera&&s.table&&!p;$('pair').hidden=linked;$('cap').style.gridColumn=linked?'1/-1':'';
$('pairhint').hidden=!p;$('cap').disabled=s.busy;$('cap').hidden=s.busy;$('stop').hidden=!s.busy;
const open=s.session_item>0;
$('sess').textContent=s.session_pending?'…':open?'Stop item #'+s.session_item:'Start item (kit)';
$('sess').disabled=!!s.session_pending||s.busy;
$('sessinfo').hidden=!open&&!s.session_note;
$('sessinfo').textContent=open?'Item #'+s.session_item+' is open: '+s.session_photos+' photos, '+s.session_videos+
' videos so far. Every capture goes into it until you stop it.':s.session_note;
$('cap').textContent=open?'Capture into item #'+s.session_item:'Start capture';
$('conn').textContent=s.busy?'busy: '+s.job:'ready to use';
$('ang').textContent=s.table?s.table_angle.toFixed(1)+'°':'–';
const free=s.table&&!s.busy,driving=s.busy&&s.job=='drive';
document.querySelectorAll('.hold').forEach(b=>b.disabled=!s.table||(s.busy&&!driving));
document.querySelectorAll('.jog,#go,#zero,#stopchips button').forEach(b=>b.disabled=!free);
$('tstop').disabled=!s.table;$('shot').disabled=$('rec').disabled=!s.camera||s.busy;
$('save').disabled=$('reset').disabled=s.busy;
$('now').textContent=s.time||'waiting for the time server…';$('clk').textContent=s.camera_clock||'–';
$('setclk').disabled=!s.camera||s.busy||!s.time;}
async function poll(){try{const r=await fetch('/status',{cache:'no-store'});s=await r.json();fails=0;show();}
catch(e){if(++fails>1)$('conn').textContent='station not responding…';}setTimeout(poll,s&&s.busy?600:1500);}
async function post(path,ok){const m=$('msg');m.className='';m.textContent='…';
try{const r=await fetch(path,{method:'POST'});m.textContent=r.ok?ok:(r.status==409?'Busy, try again in a moment':'Failed ('+r.status+')');
if(!r.ok)m.className='err';}catch(e){m.className='err';m.textContent='Station not reachable';}
setTimeout(()=>{if(m.textContent!=='…')m.textContent=''},5000);}
$('pair').onclick=()=>post('/pair','Pairing started');
$('cap').onclick=()=>post('/trigger','Capture started');
$('sess').onclick=()=>s.session_item>0?post('/session/stop','Closing item #'+s.session_item):post('/session/start','Opening an item');
$('stop').onclick=()=>post('/abort','Stopping');

// manual control
const spd=()=>$('spd').value/100;
const pct=(r,l)=>{const f=()=>$(l).textContent=$(r).value+' %';$(r).addEventListener('input',f);return f;};
const showSpd=pct('spd','spdv'),showP=pct('pspd','pspdv'),showV=pct('vspd','vspdv');
$('spd').value=localStorage.spd||30;showSpd();$('spd').addEventListener('change',()=>localStorage.spd=$('spd').value);
// One request in flight at a time while held; each hold has its own id so the station can ignore
// repeats that arrive after the release.
let cur=null;
function hold(dir,btn){release();const h={id:1+Math.floor(Math.random()*2e9),on:true,btn,t:0};cur=h;if(btn)btn.classList.add('on');
const send=async()=>{if(!h.on)return;try{await fetch('/table/drive?speed='+(dir*spd()).toFixed(3)+'&hold='+h.id,{method:'POST'});}catch(e){}
if(h.on)h.t=setTimeout(send,200);};send();}
function release(){const h=cur;if(!h)return;cur=null;h.on=false;clearTimeout(h.t);if(h.btn)h.btn.classList.remove('on');
const stop=()=>fetch('/table/drive?speed=0&hold='+h.id,{method:'POST',keepalive:true});
stop().catch(()=>setTimeout(()=>stop().catch(()=>{}),300));}
document.querySelectorAll('.hold').forEach(b=>{
b.addEventListener('pointerdown',e=>{if(b.disabled)return;b.setPointerCapture(e.pointerId);hold(+b.dataset.dir,b);});
['pointerup','pointercancel','lostpointercapture'].forEach(n=>b.addEventListener(n,release));
b.addEventListener('contextmenu',e=>e.preventDefault());});
let keyDir=0;
addEventListener('keydown',e=>{if(e.target.tagName=='INPUT'||e.repeat)return;const d=e.key=='ArrowRight'?1:e.key=='ArrowLeft'?-1:0;
if(!d||!s||!s.table)return;e.preventDefault();keyDir=d;hold(d,document.querySelector('.hold[data-dir="'+d+'"]'));});
addEventListener('keyup',e=>{if((e.key=='ArrowRight'&&keyDir==1)||(e.key=='ArrowLeft'&&keyDir==-1)){keyDir=0;release();}});
addEventListener('blur',release);
document.querySelectorAll('.jog').forEach(b=>b.onclick=()=>post('/table/jog?deg='+b.dataset.deg+'&speed='+spd(),'Turning '+b.textContent));
const goTo=d=>post('/table/goto?deg='+d+'&speed='+spd(),'Going to '+d+'°');
$('go').onclick=()=>goTo(+$('to').value||0);
$('tstop').onclick=()=>{release();post('/table/stop','Stopped');};
$('zero').onclick=()=>post('/table/zero','This position is now 0°');
$('shot').onclick=()=>post('/camera/shutter','Photo taken');
$('rec').onclick=()=>post('/camera/record','Video started / stopped');
$('setclk').onclick=async()=>{const m=$('msg');m.className='';m.textContent='Setting the camera clock…';
try{const r=await fetch('/camera/clock',{method:'POST'});const j=await r.json();
m.textContent=r.ok?'Camera clock '+j.result:'Not set: '+j.result;if(!r.ok)m.className='err';}
catch(e){m.className='err';m.textContent='Station not reachable';}};

// settings
function chips(list){const c=$('stopchips');c.innerHTML='';list.split(',').filter(x=>x!=='').forEach(a=>{
const b=document.createElement('button');b.textContent=a+'°';b.title='Go to this photo stop';b.onclick=()=>goTo(+a);c.appendChild(b);});}
function fillSettings(c){$('stops').value=c.stops;chips(c.stops);$('even').value=c.stops.split(',').length;
$('pspd').value=Math.round(c.photo_speed*100);$('vspd').value=Math.round(c.video_speed*100);showP();showV();
$('settle').value=c.settle_ms;$('wait').value=c.wait_ms;$('video').checked=!!c.video;$('dir').value=c.clockwise;
$('hpt').value=c.head_per_table;$('slow').value=c.slow_zone;$('minspd').value=Math.round(c.min_speed*100);
$('early').value=c.stop_early;$('lead').value=c.video_lead_ms;$('vc1').checked=!!c.video_c1;
$('vspd').disabled=$('vc1').disabled=!c.video;}
$('video').onchange=()=>$('vspd').disabled=$('vc1').disabled=!$('video').checked;
$('fill').onclick=()=>{const n=Math.max(1,Math.min(24,+$('even').value||1));
$('stops').value=Array.from({length:n},(_,i)=>+(360*i/n).toFixed(1)).join(',');};
async function saveSettings(body,okText){const m=$('setmsg');m.style.color='';m.textContent='Saving…';
try{const r=await fetch('/settings',{method:'POST',body});const j=await r.json();
if(r.ok){fillSettings(j);m.textContent=okText;}else{m.style.color='var(--bad)';m.textContent=j.error||('Failed ('+r.status+')');}}
catch(e){m.style.color='var(--bad)';m.textContent='Station not reachable';}}
$('save').onclick=()=>saveSettings(new URLSearchParams({stops:$('stops').value,photo_speed:$('pspd').value/100,
video_speed:$('vspd').value/100,settle_ms:$('settle').value,wait_ms:$('wait').value,video:$('video').checked?1:0,
clockwise:$('dir').value,head_per_table:$('hpt').value,slow_zone:$('slow').value,min_speed:$('minspd').value/100,
stop_early:$('early').value,video_lead_ms:$('lead').value,video_c1:$('vc1').checked?1:0}),'Saved. The next capture uses these.');
$('reset').onclick=()=>{if(confirm('Put all capture settings back to the defaults?'))saveSettings(new URLSearchParams({reset:1}),'Back to the defaults.');};
fetch('/settings').then(r=>r.json()).then(fillSettings).catch(()=>{});
poll();
</script></html>)html";

static void setupWeb() {
  web.on("/", HTTP_GET, [] { web.send_P(200, "text/html", INDEX_PAGE); });
  web.on("/devices", HTTP_GET, [] {                      // what the scans hear, newest first
    Seen copy[SEEN_MAX];
    portENTER_CRITICAL(&seenMux);
    memcpy(copy, seen, sizeof(copy));
    portEXIT_CRITICAL(&seenMux);
    std::sort(copy, copy + SEEN_MAX, [](const Seen &a, const Seen &b) { return a.at > b.at; });
    static const char *KIND[] = {"other", "headone", "sony"};
    String j = "[";
    for (int i = 0; i < SEEN_MAX; i++) {
      if (!copy[i].addr[0]) continue;
      if (j.length() > 1) j += ",";
      j += "{\"addr\":\"" + String(copy[i].addr) + "\",\"name\":" + quote(copy[i].name) + ",\"mfr\":\"" + copy[i].mfr +
           "\",\"rssi\":" + copy[i].rssi + ",\"match\":\"" + KIND[copy[i].kind] + "\",\"age_s\":" + (millis() - copy[i].at) / 1000 + "}";
    }
    reply(200, j + "]");
  });
  web.on("/pair", HTTP_POST, [] {
    int code = startPairing();
    reply(code, code == 202 ? "{\"pairing\":true}" : "{\"pairing\":false,\"reason\":\"busy\"}");
  });
  web.on("/status", HTTP_GET, [] { reply(200, statusJson()); });
  web.on("/trigger", HTTP_POST, [] { bool s = startJob(JOB_CAPTURE); reply(s ? 202 : 409, s ? "{\"started\":true}" : "{\"started\":false,\"reason\":\"busy\"}"); });
  // Start item / Stop item on the server (a kit: every run until Stop goes into one item).
  // Sent from pollTask so this answers at once; the result shows in /status session_*.
  web.on("/session/start", HTTP_POST, [] { sessionWanted = 1; reply(202, "{\"ok\":true}"); });
  web.on("/session/stop", HTTP_POST, [] { sessionWanted = 2; reply(202, "{\"ok\":true}"); });
  web.on("/abort", HTTP_POST, [] { abortRequested = true; reply(200, "{\"ok\":true}"); });
  web.on("/table/jog", HTTP_POST, [] {                   // relative turn; negative = backwards
    float deg = web.hasArg("deg") ? web.arg("deg").toFloat() : 90;
    bool s = deg != 0 && startJob(JOB_JOG, deg, argSpeed(cfg.photoSpeed));
    reply(s ? 202 : 409, s ? "{\"started\":true}" : "{\"started\":false}");
  });
  web.on("/table/goto", HTTP_POST, [] {                  // to an angle from the zero mark, shortest way
    float deg = fmodf(web.arg("deg").toFloat() + 3600.0f, 360.0f);
    bool s = web.hasArg("deg") && startJob(JOB_GOTO, deg, argSpeed(cfg.photoSpeed));
    reply(s ? 202 : 409, s ? "{\"started\":true}" : "{\"started\":false}");
  });
  web.on("/table/drive", HTTP_POST, [] {                 // hold-to-move; repeat every ~300 ms, speed=0 lets go
    float v = constrain(web.arg("speed").toFloat(), -1.0f, 1.0f);
    uint32_t id = strtoul(web.arg("hold").c_str(), nullptr, 10);
    if (fabsf(v) < 0.01f) {                              // let go
      if (id && !holdReleased(id)) { releasedHolds[releasedAt] = id; releasedAt = (releasedAt + 1) % 8; }
      driveUntil = millis();
      reply(200, "{\"ok\":true}");
      return;
    }
    if (holdReleased(id)) { reply(200, "{\"ok\":true,\"ignored\":\"released\"}"); return; }   // a late repeat
    driveSpeed = v;
    driveUntil = millis() + DRIVE_HOLD_MS;
    bool ok = busy ? currentJob == JOB_DRIVE : (startJob(JOB_DRIVE) || uxQueueMessagesWaiting(jobs) > 0);
    reply(ok ? 202 : 409, ok ? "{\"ok\":true}" : "{\"ok\":false,\"reason\":\"busy\"}");
  });
  web.on("/table/zero", HTTP_POST, [] {                  // this position is 0 deg
    if (busy) return reply(409, "{\"ok\":false}");
    double p;
    if (!head.readPan(p)) return reply(503, "{\"ok\":false,\"error\":\"no position reading\"}");
    head.setZero();
    prefs.putDouble("zeroPan", head.zeroPan());            // the zero mark survives restarts
    reply(200, "{\"ok\":true}");
  });
  web.on("/settings", HTTP_GET, [] { reply(200, settingsJson()); });
  web.on("/settings", HTTP_POST, [] {                    // any subset of the fields in GET /settings
    if (busy) return reply(409, "{\"ok\":false,\"error\":\"busy\"}");
    String err;
    Settings s = cfg;
    bool c1 = videoC1;
    if (web.hasArg("reset")) { defaultSettings(s); c1 = false; }
    else if (!settingsFromArgs(s, err)) return reply(400, "{\"ok\":false,\"error\":" + quote(err) + "}");
    else if (web.hasArg("video_c1")) c1 = web.arg("video_c1").toInt() != 0;
    videoC1 = c1;
    prefs.putBool("videoC1", videoC1);
    cfg = s;
    applySettings();
    prefs.putBytes("cfg", &cfg, sizeof(cfg));
    reply(200, settingsJson());
  });
  web.on("/table/numeric", HTTP_POST, [] {
    bool s = web.hasArg("deg") && startJob(JOB_NUMERIC, web.arg("deg").toFloat());
    reply(s ? 202 : 409, s ? "{\"started\":true,\"result_in\":\"/status last\"}" : "{\"started\":false}");
  });
  web.on("/table/stop", HTTP_POST, [] { abortRequested = true; if (!busy) head.stop(); reply(200, "{\"ok\":true}"); });
  web.on("/table/shutter", HTTP_POST, [] { reply(withLinks([] { return head.fireShutter(); }), "{}"); });
  web.on("/camera/shutter", HTTP_POST, [] { reply(withLinks([] { return camera.takePhoto(); }), "{}"); });
  web.on("/camera/record", HTTP_POST, [] {               // start / stop, with C1 held while filming if set
    int code = withLinks([] {
      bool ok;
      if (!testRecording) {
        if (videoC1) { camera.holdC1(true); delay(VIDEO_C1_SETTLE_MS); }
        ok = camera.toggleRecord();
        if (!ok && videoC1) camera.holdC1(false);
        testRecording = ok;
      } else {
        ok = camera.toggleRecord();
        if (videoC1) { delay(300); camera.holdC1(false); }
        testRecording = false;
      }
      return ok;
    });
    reply(code, String("{\"recording\":") + (testRecording ? "true" : "false") + "}");
  });
  web.on("/camera/clock", HTTP_POST, [] {                // camera date and time from the station's NTP clock
    String msg;
    int code = setCameraClock(msg);
    reply(code, String("{\"ok\":") + (code == 200 ? "true" : "false") + ",\"result\":" + quote(msg) + "}");
  });
  setupOtaWeb();
  web.begin();
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  led(40, 0, 0);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.printf("\nWi-Fi ok: http://%s/status\n", WiFi.localIP().toString().c_str());
  configTzTime(TIMEZONE, NTP_SERVER);                    // background NTP sync, re-synced hourly

  NimBLEDevice::init("AuctionStation");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  NimBLEDevice::setMTU(247);                             // HeadONE status frames are well over 100 bytes
  // Sony: legacy "just works" bonding with identity keys, like the RMT-P1BT. With Secure Connections
  // requested the camera ignores the pairing request. The HeadONE does not ask for security.
  NimBLEDevice::setSecurityAuth(true, false, false);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  NimBLEDevice::setSecurityInitKey(BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID);
  NimBLEDevice::setSecurityRespKey(BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID);
  // Sony cameras read our Device Name (GAP service 0x1800 / 0x2A00) before they answer a pairing
  // request. NimBLE only serves GAP once a GATT server is started (Bluedroid always does), so start
  // an empty one. It does not advertise. See h2zero/NimBLE-Arduino#572.
  NimBLEDevice::createServer()->start();
  NimBLEScan *scan = NimBLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ScanCallbacks());
  scan->setActiveScan(true);                             // the HeadONE's name comes in the scan response
  scan->setInterval(100);
  scan->setWindow(90);

  head.begin();
  loadSettings();
  bleLock = xSemaphoreCreateMutex();
  noteLock = xSemaphoreCreateMutex();
  jobs = xQueueCreate(1, sizeof(Job));
  xTaskCreatePinnedToCore(jobTask, "jobs", 8192, nullptr, 1, nullptr, 1);
  xTaskCreatePinnedToCore(bleTask, "ble", 8192, nullptr, 1, nullptr, 1);
  xTaskCreatePinnedToCore(pollTask, "poll", 6144, nullptr, 1, nullptr, 0);
  setupWeb();
  setupArduinoOta();
}

void loop() {
  web.handleClient();
  ArduinoOTA.handle();
  if (updating) return;                                  // no Bluetooth work or captures during an update
  updateLed();                                           // Bluetooth work is on bleTask, server check-in on pollTask

  // Button: a press during a capture stops it. Otherwise a short press captures, and holding it
  // 1 s opens an item on the server (or closes the open one): the runs in between become one item.
  static uint32_t downAt = 0, lastChange = 0;
  static bool down = false, handled = false;
  bool pressed = digitalRead(BUTTON_PIN) == LOW;
  if (pressed != down && millis() - lastChange > 40) {   // debounced edge
    down = pressed;
    lastChange = millis();
    if (down) {
      downAt = millis();
      handled = false;
      if (busy) { abortRequested = true; handled = true; }
    } else if (!handled) {
      startJob(JOB_CAPTURE);                             // short press
    }
  }
  if (down && !handled && millis() - downAt >= 1000) {   // long press
    handled = true;
    sessionWanted = sessionItem > 0 ? 2 : 1;
    led(40, 0, 40);                                      // a purple blink: the press was taken
    delay(150);
  }
}
