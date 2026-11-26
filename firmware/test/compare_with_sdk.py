"""Builds the same packets with the edelkrone SDK and compares with proto_test's output."""
import subprocess, sys
from edelkrone.protocol._generated.head_module import LibHeadModule
from edelkrone.protocol.moco import EdelkroneMoco, EdlSetup

head, moco = LibHeadModule(), EdelkroneMoco()

def enc(build):
    ptr, n = [0] * 64, [0]
    build(ptr, n)
    return " ".join(f"{b & 0xFF:02x}" for b in ptr[: ptr[0] + 2])

want = {
    "statusGet": enc(head.status_get),
    "stopMove": enc(head.keypose_abort),
    "triggerShutter": enc(head.trigger_photo_test),
    "configurationSet": enc(lambda p, n: head.configuration_set(p, n, EdlSetup.PAN_ONLY)),
    "numericRead": enc(lambda p, n: head.numeric_control_read(p, n, 255)),
    "ioctlStatusGet": enc(moco.ioctl_status_get),
    "groupIdSelfSet": enc(lambda p, n: EdelkroneMoco.group_id_self_set(p, n, 0xBEEF)),
}
for s in (0.5, -0.5, 1.0, -1.0, 0.06, -0.0333, 0.0, 2.0):
    clamped = max(-1.0, min(1.0, s))      # the SDK clamps to +-1 before building (section 4.1)
    want[f"manualPanTilt:{s:g}"] = enc(lambda p, n: head.manual_pan_tilt(p, n, clamped, 0.0))
for d in (90.0, -90.0, 359.999, 12.345):
    want[f"numericPanMove:{d:g}"] = enc(lambda p, n: head.numeric_control_pan_move(p, n, d))

got = {}
for line in subprocess.run([sys.argv[1]], capture_output=True, text=True, check=True).stdout.splitlines():
    if line.startswith(("reply", "corrupted")):
        print(line)
        continue
    name, _, hexes = line.partition(" ")
    got[name] = hexes
bad = 0
for name, expected in want.items():
    ok = got.get(name) == expected
    bad += not ok
    print(("OK  " if ok else "DIFF"), name, got.get(name), "" if ok else f"| sdk: {expected}")
print("ALL MATCH" if not bad else f"{bad} MISMATCHES")

# reply parsing: same bytes through the SDK's own parsers
from edelkrone.protocol._generated.head_module import LibHeadModuleMk2DigitalReadout, LibHeadModuleMk2Status
from edelkrone.protocol._generated.head_module_util import LibHeadModuleUtil
r = [0] * 30; r[0], r[1] = 18, 255
LibHeadModuleUtil.put32b(r, 2, -123456 & 0xFFFFFFFF)
r[28:30] = [(sum(r[:28]) >> 8) & 0xFF, sum(r[:28]) & 0xFF]
ro = LibHeadModuleMk2DigitalReadout(); idx = [0]
head.numeric_control_parse(r, idx, ro)
print(f"sdk reply checksum_ok={EdelkroneMoco.checksum_control(r, 30) == 0} index={idx[0]} pan={ro.pan_deg:.3f}")
st = [0] * 140; st[0:7] = [2, 12, 0, 1, 0, 0, 1]; st[17] = 9
s = LibHeadModuleMk2Status(); head.status_parse(st, s)
print(f"sdk status state={s.state.value} config={s.configuration.value} battery={s.battery_level} calibrated={s.head_calibrated}")
