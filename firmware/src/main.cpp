// Auction station controller (ESP32-S3). Runs the whole capture on its own:
//   button -> open an item on the server -> 4 x (photo, turn 90 deg) -> film one full turn -> tell the server.
// Holds two Bluetooth links at once: the Sony camera and the edelkrone HeadONE turntable.
// The camera uploads the files to the server by itself over Wi-Fi (FTP).
//
// HTTP API (port 80), for testing from a browser or curl:
//   GET  /status                  everything at a glance (JSON)
//   POST /trigger                 run the capture (same as the button)
//   POST /abort                   stop the running job and the table
//   POST /table/jog?deg=90&speed=0.5   turn the table, for calibration
//   POST /table/stop
//   POST /table/numeric?deg=90    experimental: head's own planned pan move, reports before/after angle
//   POST /table/shutter           fire the HeadONE's camera-port shutter
//   POST /camera/shutter, /camera/record
//   GET  /update                  firmware upload page (user admin, OTA_PASSWORD)
//   POST /update                  new firmware: curl -u admin:PASS -F firmware=@firmware.bin http://<esp>/update
// Also answers PlatformIO's espota uploads on port 3232 (pio run -e ota -t upload).

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <NimBLEDevice.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>

#include "config.h"
#include "headone.h"
#include "sony.h"

static WebServer web(80);
static HeadOne head;
static SonyRemote camera;

// ---- Bluetooth discovery (the scan callback runs on the BLE task; connecting happens in loop) ----
static volatile bool headFound = false, cameraFound = false;
static NimBLEAddress headAddress, cameraAddress;
static std::string headAdv;
static String headError = "searching", cameraError = "searching";

class ScanCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice *dev) override {
    if (!headFound && !head.connected() && HeadOne::isHeadOne(dev)) {
      headAddress = dev->getAddress();
      headAdv = dev->getServiceDataCount() ? dev->getServiceData((uint8_t)0) : std::string();
      headFound = true;
    } else if (!cameraFound && !camera.connected() && SonyRemote::isSonyCamera(dev)) {
      cameraAddress = dev->getAddress();
      cameraFound = true;
    }
  }
};

// ---- jobs run on their own task so the web server and button stay responsive ----
enum JobKind { JOB_CAPTURE, JOB_JOG, JOB_NUMERIC };
struct Job { JobKind kind; float deg; float speed; };
static QueueHandle_t jobs;
static volatile bool busy = false, abortRequested = false;
static String lastResult = "idle";
static int lastItem = -1;

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

static int openItem() {
  String resp;
  int code = serverPost("/api/items", String("{\"photos\":") + PHOTOS + ",\"videos\":" + (RECORD_VIDEO ? 1 : 0) + "}", resp);
  int at = resp.indexOf("\"id\"");
  if (code != 200 || at < 0) {
    Serial.printf("Server did not open an item (%d %s); the photos will become a manual item\n", code, resp.c_str());
    return -1;
  }
  return resp.substring(resp.indexOf(':', at) + 1).toInt();
}

// ---- the capture ----
static bool shoot() { return SHUTTER_VIA_HEAD ? head.fireShutter() : camera.takePhoto(); }

static bool capture(String &err) {
  if (!head.connected()) { err = "turntable not connected: " + headError; return false; }
  if ((!SHUTTER_VIA_HEAD || RECORD_VIDEO) && !camera.connected()) { err = "camera not connected: " + cameraError; return false; }
  lastItem = openItem();
  Serial.printf("Capture started, item %d\n", lastItem);
  for (int n = 0; n < PHOTOS; n++) {
    delay(SETTLE_BEFORE_PHOTO_MS);
    if (abortRequested) { err = "stopped by request"; return false; }
    if (!shoot()) { err = String("photo ") + (n + 1) + " failed"; return false; }
    delay(WAIT_AFTER_PHOTO_MS);
    if (!head.turnBy(360.0f / PHOTOS, PHOTO_TURN_SPEED, abortRequested, err)) return false;
  }
  if (RECORD_VIDEO) {
    delay(SETTLE_BEFORE_PHOTO_MS);
    if (!camera.toggleRecord()) { err = "could not start the video"; return false; }
    delay(VIDEO_LEAD_MS);
    bool turned = head.turnBy(360.0f, VIDEO_TURN_SPEED, abortRequested, err);
    delay(VIDEO_LEAD_MS);
    camera.toggleRecord();                     // stop filming even if the turn failed
    if (!turned) return false;
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
    busy = true;
    abortRequested = false;
    String err, report;
    bool ok = false;
    switch (j.kind) {
      case JOB_CAPTURE: ok = capture(err); if (ok) report = String("item ") + lastItem + " captured"; break;
      case JOB_JOG: ok = head.turnBy(j.deg, j.speed, abortRequested, err); if (ok) report = String("turned ") + j.deg; break;
      case JOB_NUMERIC: ok = head.numericMove(j.deg, report); break;
    }
    if (!ok) head.stop();
    lastResult = ok ? report : "error: " + err;
    Serial.println(lastResult);
    busy = false;
  }
}

// ---- status LED: red nothing, yellow one device, green ready, blue busy, flashing red error ----
static void led(uint8_t r, uint8_t g, uint8_t b) {
  if (LED_RGB_PIN >= 0) neopixelWrite(LED_RGB_PIN, r, g, b);
}

static void updateLed() {
  bool cam = camera.connected() || (SHUTTER_VIA_HEAD && !RECORD_VIDEO), table = head.connected();
  if (busy) led(0, 0, 40);
  else if (lastResult.startsWith("error") && (millis() / 300) % 2) led(60, 0, 0);
  else if (cam && table) led(0, 40, 0);
  else if (cam || table) led(40, 25, 0);
  else led(40, 0, 0);
}

// ---- web ----
static void reply(int code, const String &json) { web.send(code, "application/json", json); }
static String quote(const String &s) { String q = s; q.replace("\\", "\\\\"); q.replace("\"", "\\\""); return "\"" + q + "\""; }

// ---- over-the-air updates ----
static volatile bool updating = false;
static String updateError;

static void prepareForUpdate() {
  updating = true;
  abortRequested = true;                                 // stop a running capture and the table
  for (int i = 0; i < 50 && busy; i++) delay(100);
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

static void setupWeb() {
  web.on("/status", HTTP_GET, [] {
    String j = "{";
    j += "\"busy\":" + String(busy ? "true" : "false");
    j += ",\"last\":" + quote(lastResult);
    j += ",\"last_item\":" + String(lastItem);
    j += ",\"camera\":" + String(camera.connected() ? "true" : "false") + ",\"camera_note\":" + quote(cameraError);
    j += ",\"table\":" + String(head.connected() ? "true" : "false") + ",\"table_note\":" + quote(headError);
    j += ",\"table_pan_deg\":" + String(head.lastPan(), 2) + ",\"table_battery\":" + String(head.battery());
    j += ",\"table_mtu\":" + String(head.mtu()) + ",\"table_bad_frames\":" + String(head.badFrames());
    j += ",\"wifi_rssi\":" + String(WiFi.RSSI()) + "}";
    reply(200, j);
  });
  web.on("/trigger", HTTP_POST, [] { bool s = startJob(JOB_CAPTURE); reply(s ? 202 : 409, s ? "{\"started\":true}" : "{\"started\":false,\"reason\":\"busy\"}"); });
  web.on("/abort", HTTP_POST, [] { abortRequested = true; reply(200, "{\"ok\":true}"); });
  web.on("/table/jog", HTTP_POST, [] {
    float deg = web.hasArg("deg") ? web.arg("deg").toFloat() : 90;
    float speed = web.hasArg("speed") ? web.arg("speed").toFloat() : PHOTO_TURN_SPEED;
    bool s = deg > 0 && startJob(JOB_JOG, deg, speed);
    reply(s ? 202 : 409, s ? "{\"started\":true}" : "{\"started\":false}");
  });
  web.on("/table/numeric", HTTP_POST, [] {
    bool s = web.hasArg("deg") && startJob(JOB_NUMERIC, web.arg("deg").toFloat());
    reply(s ? 202 : 409, s ? "{\"started\":true,\"result_in\":\"/status last\"}" : "{\"started\":false}");
  });
  web.on("/table/stop", HTTP_POST, [] { abortRequested = true; if (!busy) head.stop(); reply(200, "{\"ok\":true}"); });
  web.on("/table/shutter", HTTP_POST, [] { reply(head.fireShutter() ? 200 : 503, "{}"); });
  web.on("/camera/shutter", HTTP_POST, [] { reply(camera.takePhoto() ? 200 : 503, "{}"); });
  web.on("/camera/record", HTTP_POST, [] { reply(camera.toggleRecord() ? 200 : 503, "{}"); });
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

  NimBLEDevice::init("AuctionStation");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  NimBLEDevice::setMTU(247);                             // HeadONE status frames are well over 100 bytes
  NimBLEDevice::setSecurityAuth(true, false, true);      // Sony bonds; the HeadONE does not ask for it
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  NimBLEScan *scan = NimBLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ScanCallbacks());
  scan->setActiveScan(true);                             // the HeadONE's name comes in the scan response
  scan->setInterval(100);
  scan->setWindow(90);

  head.begin();
  jobs = xQueueCreate(1, sizeof(Job));
  xTaskCreatePinnedToCore(jobTask, "jobs", 8192, nullptr, 1, nullptr, 1);
  setupWeb();
  setupArduinoOta();
}

void loop() {
  web.handleClient();
  ArduinoOTA.handle();
  if (updating) return;                                  // no Bluetooth work or captures during an update
  updateLed();

  static uint32_t lastScan = 0, lastPress = 0;
  NimBLEScan *scan = NimBLEDevice::getScan();
  if (!busy && (headFound || cameraFound) && scan->isScanning()) scan->stop();
  if (!busy && headFound && !scan->isScanning()) {
    String err;
    headError = head.connect(headAddress, headAdv, err) ? "ok" : err;
    if (headError != "ok") Serial.println(headError);
    headFound = false;
  }
  if (!busy && cameraFound && !scan->isScanning()) {
    String err;
    cameraError = camera.connect(cameraAddress, err) ? "ok" : err;
    if (cameraError != "ok") Serial.println(cameraError);
    cameraFound = false;
  }
  bool needCamera = !SHUTTER_VIA_HEAD || RECORD_VIDEO;
  if (!busy && (!head.connected() || (needCamera && !camera.connected())) && !scan->isScanning() &&
      millis() - lastScan > 3000) {
    lastScan = millis();
    scan->start(5, nullptr, false);                      // 5 s, non-blocking
  }

  if (digitalRead(BUTTON_PIN) == LOW && millis() - lastPress > 1500) {
    delay(40);
    if (digitalRead(BUTTON_PIN) == LOW) {
      lastPress = millis();
      if (busy) abortRequested = true;                   // a press during a capture stops it
      else startJob(JOB_CAPTURE);
    }
  }
}
