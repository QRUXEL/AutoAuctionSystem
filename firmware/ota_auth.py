# PlatformIO extra script for the "ota" environment: takes the espota password from
# include/secrets.h (OTA_PASSWORD), so it never has to be written into platformio.ini.
import re
from pathlib import Path

Import("env")  # noqa: F821  (provided by PlatformIO)

secrets = Path(env.subst("$PROJECT_DIR")) / "include" / "secrets.h"  # noqa: F821
match = re.search(r'#define\s+OTA_PASSWORD\s+"([^"]*)"', secrets.read_text(encoding="utf-8")) if secrets.exists() else None
if match:
    env.Append(UPLOAD_FLAGS=["--auth=" + match.group(1)])  # noqa: F821
else:
    print("ota_auth.py: no OTA_PASSWORD in include/secrets.h; the upload will be refused")
