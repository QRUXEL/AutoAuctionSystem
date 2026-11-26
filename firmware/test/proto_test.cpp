// Host-side check: prints packets built by edelkrone_proto.h, one per line, for compare_with_sdk.py.
#include <cstdio>
#include "../src/edelkrone_proto.h"
static void out(const char *name, const ek::Packet &p) {
  printf("%s", name);
  for (size_t i = 0; i < p.len; i++) printf(" %02x", p.data[i]);
  printf("\n");
}
int main() {
  out("statusGet", ek::statusGet());
  out("stopMove", ek::stopMove());
  out("triggerShutter", ek::triggerShutter());
  out("configurationSet", ek::configurationSet(ek::SETUP_PAN_ONLY));
  out("numericRead", ek::numericRead(255));
  const float speeds[] = {0.5f, -0.5f, 1.0f, -1.0f, 0.06f, -0.0333f, 0.0f, 2.0f};
  for (float s : speeds) { char n[40]; snprintf(n, sizeof n, "manualPanTilt:%g", s); out(n, ek::manualPanTilt(s, 0)); }
  const float degs[] = {90.0f, -90.0f, 359.999f, 12.345f};
  for (float d : degs) { char n[40]; snprintf(n, sizeof n, "numericPanMove:%g", d); out(n, ek::numericPanMove(d)); }
  out("ioctlStatusGet", ek::ioctlStatusGet());
  out("groupIdSelfSet", ek::groupIdSelfSet(0xBEEF));
  // replies: pan readout of -123.456 deg built by hand, and checksum check
  uint8_t r[30] = {18, 255};
  ek::put32(r, 2, -123456);
  uint32_t sum = 0; for (int i = 0; i < 28; i++) sum += r[i]; ek::put16(r, 28, sum & 0xFFFF);
  double pan = 0;
  bool sumOk = ek::checksumOk(r, 30), parsed = ek::parsePanDeg(r, 30, 255, pan);
  printf("reply checksum=%d parsed=%d pan=%.3f\n", sumOk, parsed, pan);
  // a status frame: state 12 (numeric move running), layout 1 (pan only), battery nibble 9 -> 60 %
  uint8_t st[40] = {2, 12, 0, 1, 0, 0, 1};
  st[17] = 9;
  ek::Status status{};
  bool stOk = ek::parseStatus(st, 40, status);
  printf("status parsed=%d state=%d config=%d battery=%d calibrated=%d\n",
         stOk, status.state, status.configuration, status.battery, status.headCalibrated);
  r[5] ^= 1;
  printf("corrupted checksum=%d\n", ek::checksumOk(r, 30));
}
