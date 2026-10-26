"""
Fast alternative to set_nvcp_colors.py: instead of driving the NVIDIA
Control Panel UI, build a Brightness/Contrast/Gamma ramp from profile.json
and load it straight into the display's hardware gamma table with the
Win32 SetDeviceGammaRamp call. Takes milliseconds, no window opens.

NVCP's own color settings live in the driver, separate from this ramp, so
the two stack on top of each other. Leave NVCP's "Adjust desktop color
settings" at neutral (run `set_nvcp_colors.py off` once) and use only this
script for toggling.

The curve approximates NVCP's sliders (Brightness/Contrast 50% = neutral,
Gamma 1.00 = neutral); if "on" looks slightly different from what NVCP
produced, tweak the numbers in profile.json.

Usage:
    python fast_gamma.py          # "on" profile
    python fast_gamma.py on
    python fast_gamma.py off
"""

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

PROFILE_PATH = Path(__file__).with_name("profile.json")
# Only touch displays driven by this adapter (the laptop's Intel panel is left alone).
ADAPTER_MATCH = "NVIDIA"

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


def load_profile(name):
    profiles = json.loads(PROFILE_PATH.read_text())
    if name not in profiles:
        raise KeyError(f"No '{name}' profile in {PROFILE_PATH.name} (available: {list(profiles)})")
    return profiles[name]


def build_ramp(brightness_percent, contrast_percent, gamma_value):
    brightness_offset = (brightness_percent - 50.0) / 100.0
    contrast_factor = contrast_percent / 50.0
    ramp = (wintypes.WORD * 768)()
    for i in range(256):
        value = (i / 255.0) ** (1.0 / gamma_value)
        value = (value - 0.5) * contrast_factor + 0.5 + brightness_offset
        word = round(max(0.0, min(1.0, value)) * 65535)
        ramp[i] = ramp[256 + i] = ramp[512 + i] = word
    return ramp


def target_displays():
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            return
        if device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP and ADAPTER_MATCH in device.DeviceString:
            yield device.DeviceName
        index += 1


def main():
    profile_name = sys.argv[1] if len(sys.argv) > 1 else "on"
    profile = load_profile(profile_name)
    ramp = build_ramp(float(profile["brightness"]), float(profile["contrast"]), float(profile["gamma"]))

    displays = list(target_displays())
    if not displays:
        sys.exit(f"No active display found on an adapter matching '{ADAPTER_MATCH}'")

    failed = False
    for name in displays:
        hdc = gdi32.CreateDCW(name, None, None, None)
        ok = gdi32.SetDeviceGammaRamp(hdc, ramp)
        gdi32.DeleteDC(hdc)
        print(f"{name}: {'ok' if ok else 'REJECTED'}")
        failed |= not ok

    if failed:
        # Windows refuses ramps that stray far from identity unless this is set.
        sys.exit(
            "Windows rejected the ramp. Allow full-range ramps once (admin), then sign out/in:\n"
            '  reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ICM" '
            "/v GdiIcmGammaRange /t REG_DWORD /d 256 /f"
        )
    print(f"Profile '{profile_name}' applied.")


if __name__ == "__main__":
    main()
