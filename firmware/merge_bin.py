# PlatformIO post-build step: writes .pio/build/<env>/auction-station-full.bin, one image
# (bootloader + partitions + boot_app0 + app) to flash at offset 0x0 with a browser flasher.
Import("env")  # noqa: F821  (provided by PlatformIO)


def merge_bin(source, target, env):
    out = env.subst("$BUILD_DIR/auction-station-full.bin")
    images = [str(x) for x in env.Flatten(env.get("FLASH_EXTRA_IMAGES", []))]
    images += [env.subst("$ESP32_APP_OFFSET"), target[0].get_abspath()]  # the app .bin (source[0] is the .elf)
    quoted = [f'"{env.subst(p)}"' for p in images]
    cmd = ['"$PYTHONEXE"', '"$OBJCOPY"', "--chip", env.BoardConfig().get("build.mcu", "esp32s3"),
           "merge_bin", "-o", f'"{out}"', "--flash_mode", "dio",
           "--flash_size", env.BoardConfig().get("upload.flash_size", "4MB")] + quoted
    env.Execute(" ".join(cmd))
    print(f"Merged image for the browser flasher (offset 0x0): {out}")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge_bin)  # noqa: F821
