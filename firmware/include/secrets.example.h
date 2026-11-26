// Copy to secrets.h (same folder) and fill in. secrets.h is ignored by git.
#pragma once

#define WIFI_SSID      "QRUXEL-IoT"
#define WIFI_PASSWORD  "wifi-password"

// Over-the-air updates: http://<esp>/update (user "admin"), curl, or PlatformIO env "ota".
// Put the same value after --auth= in platformio.ini.
#define OTA_PASSWORD   "change-me"

// Same value as STATION_KEY in the server's .env
#define STATION_KEY    "change-me"
