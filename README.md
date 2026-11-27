# Auction station

One button press photographs an item from four sides, films one full turn, and
puts a framed, AI-described listing in a review queue. A person checks it and
publishes it to Tradera with one tap.

## How it fits together

```
                    +--Bluetooth--> [edelkrone HeadONE turntable]
[Button + ESP32-S3] |
                    +--Bluetooth--> [Sony A7 IV] --Wi-Fi FTP--> [Server on local Coolify]
        |                                                         crop + frame -> AI text
        +------------------Wi-Fi (open item / done)------------>  -> review page -> Tradera
```

No computer is needed at the photo table. The ESP32-S3 runs the whole capture and
holds both Bluetooth links.

| Part | Folder | Runs on | Job |
|---|---|---|---|
| Station controller | `firmware/` | ESP32-S3 | Button; drives the HeadONE; fires the camera's shutter and movie button; tells the server when an item starts and ends |
| Server | `server/` | Local Coolify | Receives the camera's files, crops and frames them, drafts the text, hosts the review page, publishes to Tradera |
| Test tools | `tools/` | anywhere | Fake camera and mock Tradera for rehearsing without hardware |
| PC agent (optional) | `station/` | a PC | The same sequence using edelkrone's own Python SDK; a fallback if the ESP32's turntable link misbehaves |

The HeadONE code in `firmware/src/edelkrone_proto.h` is ported from edelkrone's
MIT-licensed Python SDK 0.1.4. `firmware/test/` rebuilds every packet with the SDK
itself and compares them byte for byte.

## What has and has not been tested

| Piece | State |
|---|---|
| Server: FTP intake, grouping files into items, crop, frame, review API, publish flow | Run end to end with the fake camera and the mock Tradera |
| HeadONE packets (status, position, joystick, stop, layout setup, shutter, numeric move) | Identical to edelkrone's SDK output; replies parsed identically |
| ESP32-S3 firmware as a whole | **Not compiled and not run** (no ESP32 toolchain here) |
| HeadONE connection flow and turn accuracy | **Not run on a HeadONE** |
| Sony Bluetooth remote | **Not run**; community-documented protocol |
| Tradera client | Written from Tradera's OpenAPI spec and run against the mock; **not run against the real API** |
| AI description | Code path present; **not run** (no API key in the build environment) |
| Review site (React + Mantine) | Type-checked, built, and clicked through in headless Chrome against the local server: lists, item page, crop dialog, settings, phone layout |

## First test on the real hardware

Flash, open the serial monitor, and use the ESP32's HTTP API (its address is printed at boot):

1. `GET /status`: `table` and `camera` should both be `true`. If not, `table_note` / `camera_note` say why.
   `table_mtu` should be well above 100 and `table_bad_frames` should stay at 0; if frames are bad, the MTU is the suspect.
2. `POST /table/jog?deg=90` four times: the table should end where it started. Tune `STOP_EARLY_DEG`, `PHOTO_TURN_SPEED`
   and, if the turntable kit is geared, `HEAD_DEG_PER_TABLE_DEG` in `config.h`. Flip `TABLE_CLOCKWISE` if needed.
3. Optional: `POST /table/numeric?deg=90`, then `GET /status` and read `last`. It shows the angle before and after.
   If the head moves smoothly to the commanded angle, the planned move could replace the joystick loop later.
4. `POST /camera/shutter` and `POST /camera/record` (twice) to check the camera link.
5. Press the button for a full run, and watch the review page.

Check also:
- **Movies over FTP.** Confirm the A7 IV transfers the movie automatically like the stills. If not, the listing still completes and the clip stays on the card.
- **Bluetooth remote and Wi-Fi FTP together.** If the camera drops FTP while `Bluetooth Rmt Ctrl` is on, cable the camera to the HeadONE's camera port and set `SHUTTER_VIA_HEAD 1` (stills then go through the head; the video still needs Bluetooth).
- **Movie in stills mode.** The record command acts like the MOVIE button, so the dial can stay on stills.

## Setup

### 1. Tradera access
Tradera's API has no drafts: a listing is either uncommitted (invisible) or live. The review page is the draft stage.

1. Register a developer account and an application at https://api.tradera.com/register.
2. Get a user token for the selling account (Tradera's token login flow, see their Authorization docs).
3. Enter app ID, app key, user ID and token on the review site's **Settings** page and press **Test connection**.
4. Set the listing defaults (type, duration, start price, shipping options) on the same page. Shipping ids come from `GET /v4/reference-data/shipping-options`.
5. Leave **Dry run** on until a dry run looks right: Send then only writes `tradera_payload.json` next to the photos.

**Send, then Go live.** With **Auto-publish** off (the default), **Send** creates the listing on Tradera with `autoCommit: false`
and uploads the photos, then waits until Tradera reports it ready. The item shows *On Tradera, not live* and is listed under
**Waiting to go live**; nobody can see it or bid until **Go live** commits it. If the item is edited after sending, Go live is refused
and the button becomes **Send again**, which sends a fresh copy (Tradera's API has no way to remove the unpublished one; it is never
published). With Auto-publish on, Send commits straight away. Tradera's docs don't say whether unpublished listings show on
tradera.com or how long they are kept, so don't leave them waiting for days.

**When auctions end** (Settings): after a number of days, all at one fixed time, or one after another (the first at a set
time, each next one sent X minutes after the previous one). An item can have its own end time, which keeps it out of the line-up;
an item sent again keeps its end time. Times are Swedish time, sent to Tradera as `customEndDate`.

**Shipping and buyers** (Settings, and per item): Tradera's own shipping products and buyer regions are fetched and cached for a
day, and chosen with checkboxes and a dropdown; no ids to type. **Buyer collects** adds pickup: sent as Tradera's pickup option when
its lists have one, and always written in the shipping note ("Kan hämtas hos oss i …"). **Free with DSV in Sweden** picks the DSV
options at 0 kr; **+ DSV sponsors** adds a line saying so to the shipping note.

**Categories.** The AI picks the category from Tradera's own tree in two steps: first the section (e.g. *Hem & Hushåll > Porslin & Glas*),
then the category inside it, with word matches added. If it can't decide, the item's notes say so and the category is left for you.

**Charity auctions (Musikhjälpen):** set the Tradera campaign code on the Settings page; it is sent with every listing as
`campaignCode`, and checked with Tradera when entered and before each listing (against the item's category). Tradera's API cannot
list campaign codes; **Try likely Musikhjälpen codes** asks Tradera about a few guesses. The charity numbers on tradera.com
(Musikhjälpen = 3164) are not campaign codes; Tradera rejects them. While a code is rejected, nothing is sent; clear it to send
ordinary auctions.

The Claude API key for descriptions is set on the Settings page too. Values saved there override the environment variables in Coolify.

**Videos:** Tradera's API takes images only, so each item's spin video goes to a public page on https://streamhjalpen.tv and the
listing links to it. Set `AUCTION_VIDEO_SECRET` in Coolify (and keep `AUCTION_VIDEO_BASE_URL=https://streamhjalpen.tv`: other host
names redirect, which breaks uploads). As soon as the web copy is ready, the server uploads it, then the framed hero photo as poster;
the title follows when it is edited, and the Tradera item ID once the listing is live (the page then shows "Lägg bud på Tradera").
Write `<video link>` in the description or footer where the link should go; without it, a "Se videon: …" line is added at the end. The Settings page shows where videos go and has a **Test** button for the address and secret. Publishing waits for the upload, so no listing goes out
with a dead link. Deleting an item, or moving its video out, removes the video from the site. Each item's progress is shown on its
page, with **Retry** after an error; 400/401/413 answers are not retried on their own, network errors and 5xx are (5 attempts with
backoff, then again every minute). Without the secret, videos use this server's own `/v/<token>` pages instead.
`python tools/test_video_host.py` tests the uploader; `tools/mock_video_site.py` stands in for the site in rehearsals.

### 2. Server (Coolify)
1. Copy `server/.env.example` to `.env` and fill it in. `FTP_PUBLIC_HOST` must be the LAN address the camera uses to reach the server.
2. Deploy `server/` as a Docker Compose resource. Give port 8080 a domain; ports 2121 and 30000-30009 are published directly for FTP.
3. Set up the frame, logo and texts on the site's **Photo layout** page (stored in the `station-branding` volume as `layout.json`, with uploaded graphics and fonts in `assets/`).

### 3. Camera (A7 IV)
- File format JPEG (HEIF and RAW are ignored by the server).
- Network > FTP Transfer: server address, port 2121, passive mode, the FTP user and password; automatic transfer on.
- Network > Bluetooth: function on, `Bluetooth Rmt Ctrl` on; choose Pairing the first time the ESP32 starts.
- Fixed exposure, focus and white balance so all four sides match.

### 4. HeadONE
Update its firmware with the edelkrone app, mount it upright (pan) on the turntable kit, and close the app so it does not hold the Bluetooth link.

### 5. ESP32-S3
Copy `firmware/include/secrets.example.h` to `secrets.h` and fill in the Wi-Fi password, `OTA_PASSWORD` and `STATION_KEY`
(this file is not in git). Put the same OTA password after `--auth=` in `platformio.ini`.
Edit `firmware/include/config.h` for the server's LAN address, pins and sequence timing.

**First flash, building on one computer and flashing from another:**
1. Build: in `firmware/`, run `pio run -e esp32s3`. It writes `.pio/build/esp32s3/auction-station-full.bin`.
2. On the computer with the ESP32-S3 plugged in, open https://espressif.github.io/esptool-js/ in Chrome or Edge,
   Connect, pick the board's port, add `auction-station-full.bin` at offset `0x0`, Program.
   (If the board won't connect, hold BOOT while plugging it in.)
   If the merged file is missing, flash these four instead: `0x0` bootloader.bin, `0x8000` partitions.bin,
   `0xe000` boot_app0.bin (in `~/.platformio/packages/framework-arduinoespressif32/tools/partitions/`), `0x10000` firmware.bin.
3. Press RESET. It joins Wi-Fi as `auction-station` and is at http://auction-station.local (or the IP your router shows).

**Updates over Wi-Fi after that**, any of:
- Browser: http://auction-station.local/update, user `admin`, the OTA password, upload `.pio/build/esp32s3/firmware.bin`.
- curl: `curl -u admin:PASS -F firmware=@.pio/build/esp32s3/firmware.bin http://auction-station.local/update`
- PlatformIO on the same network: `pio run -e ota -t upload`

An update stops a running capture and the table first. Use `firmware.bin` (not the merged file) for updates.

Directly over USB on the same computer: `pio run -e esp32s3 -t upload && pio device monitor`.
The LED shows the state: red = nothing connected, yellow = one device, green = ready, blue = running, flashing red = last run failed.
Pressing the button during a run stops it.

**Starting from the website:** the top bar of the review site shows the station's state and has **Start capture** (and **Stop**
while it runs). The ESP32 checks in with the server every second (`POST /api/station/poll` with `X-Station-Key`) and picks up
the command, so the server never has to reach the ESP32 and nothing needs opening in the firewall. A command that isn't picked
up within 20 s is dropped. The station shows as offline when it hasn't checked in for 5 s. This needs firmware
`2026-11-27.1` or later, and `SERVER_URL` in `config.h` must be an address the ESP32 can reach.

### Packet test (optional, on any PC)
```
g++ -std=c++17 -o proto_test firmware/test/proto_test.cpp
pip install edelkrone && python firmware/test/compare_with_sdk.py ./proto_test
```

## Rehearsal without hardware
```
python tools/mock_tradera.py 9099
cd server && DATA_DIR=./data STATION_KEY=k TRADERA_BASE=http://127.0.0.1:9099 TRADERA_DRY_RUN=0 \
  TRADERA_APP_ID=1 TRADERA_APP_KEY=k TRADERA_USER_ID=2 TRADERA_USER_TOKEN=t uvicorn app.main:app --port 8080
python station/agent.py --simulate           # stands in for the ESP32; [server] url = http://127.0.0.1:8080, same key
curl -X POST http://127.0.0.1:8765/trigger   # the "button"
python tools/fake_camera.py                  # the "camera"
```
Then open http://127.0.0.1:8080.

## Review site
React + Mantine (dark), in `server/web/`. The Docker image builds it; for local work:
```
cd server/web && npm install && npm run dev     # proxies /api to a server on :8080
```
Photos can be re-cropped per item (the crop box has the frame's shape); the manual crop is kept when photos are re-framed.
Photos can be put in order by dragging the thumbnails (or with the arrows, on a phone); **Make main photo** puts one first. The first
photo is the listing's main photo and thumbnail on Tradera, and the video's poster. **Remove** moves a photo to Loose shots, where
it can be deleted or added back. A manual order is kept when photos are added; **Shooting order** puts them back.

## Photo layout
Every length is a percentage of the photo's short side, and every logo, text or bar is pinned to a corner or edge of the photo, the whole image, or one of the margins. So one layout fits landscape, portrait and square photos alike. The output either keeps each photo's (cropped) shape or crops every photo to a fixed shape. Texts can use `{ref}` (item number) and `{date}`. The editor's previews are rendered by the same code as the real photos; **Re-frame unpublished items** applies a changed layout to items not yet published, keeping manual crops.

## Items with several parts (Start item / Stop item)
For a lot with several things, or anything shot by hand: press **Start item** in the top bar, shoot as many photos and videos as
needed (by hand, and/or with **Start capture** turntable runs, which then join the open item instead of opening their own), and press
**Stop item**. Everything shot in between goes into that one item, in shooting order; test shots before Start or after Stop go to
Loose shots. The item is processed once the last uploads are in (`GRACE_SECONDS` after Stop or the last upload). An item left open is
closed after 12 hours. `python tools/test_session.py` runs the scenario against a local server.

## How files become items
Matching is by time, so test shots never end up in a listing:

1. The ESP32 reports the start of a capture just before the first photo and the end just after the video; the server stamps both on its own clock.
2. Each file's shot time is read from the file itself (EXIF for photos, the MP4 header for videos).
3. The camera's clock is rarely right, so the server measures the offset: a photo arrives a few seconds after it is shot, and the smallest (arrival - shot time) over recent photos is the offset. The Loose shots page shows it when it is more than a few seconds. Video headers are sometimes in UTC; whole-hour differences are tried for those.
4. A file shot inside a capture's window (plus 3 s) goes to that item, however late it is uploaded. A photo that arrives after its item was processed is framed in.
5. Everything else lands in **Loose shots**, where you can delete it, add it to an item, or make a new item from it. A wrong photo can also be moved out of an item.

An item is processed once all its photos are in, or `GRACE_SECONDS` (default 120, set on the Settings page) after the end of the capture and the last upload. `python tools/test_matching.py` runs the whole scenario against a local server.
