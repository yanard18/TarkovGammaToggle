"""
Opens NVIDIA Control Panel, navigates to "Adjust desktop color settings",
sets Brightness/Contrast/Gamma via the real sliders (values read from
profile.json), clicks Apply, then closes the window.

Changing a slider only marks the page "dirty" - it does not commit the
value. An "Apply Bar" with Apply/Cancel buttons appears at the bottom of
the window once there's a pending change. If you close the window without
clicking Apply, NVCP raises a blocking "Apply Changes" dialog ("You have
changes that have not been applied yet. Would you like to apply these
changes now?" / Yes/No/Cancel) instead of just closing - so this script
clicks Apply itself before closing, and clicks "Yes" on that dialog too
as a safety net in case it appears anyway.

Usage:
    python set_nvcp_colors.py          # uses the "on" profile from profile.json
    python set_nvcp_colors.py on       # same, explicit
    python set_nvcp_colors.py off      # uses the "off" profile from profile.json

Control identifiers (auto_id, control_type) and the Gamma slider's 0-100 ->
0.30-2.80 scale were confirmed by inspecting the live NVIDIA Control Panel
window (package NVIDIACorp.NVIDIAControlPanel) with pywinauto, and verified
by reading back its own on-screen "65%" / "2.15" labels after setting them.

Note on running headless/minimized: tested and not applied here. The
window can be told to minimize, and pattern-based calls (TreeItem.select(),
Slider.set_value(), Button.invoke()) don't need mouse clicks - but this
NVCP build suspends its UI Automation tree while minimized (XAML/UWP
content isn't kept "live" off-screen), so calls made too soon after
minimizing intermittently fail with a COM "element not available" error.
It's a timing-dependent race, not a reliable change, so the window is
left visible for the brief time it takes to set values and close.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from pywinauto import Application
from pywinauto.findwindows import ElementNotFoundError

WINDOW_TITLE = "NVIDIA Control Panel"
COLOR_SETTINGS_TREE_ITEM = "Adjust desktop color settings"
BRIGHTNESS_AUTO_ID = "539"
CONTRAST_AUTO_ID = "569"
GAMMA_AUTO_ID = "600"
GAMMA_LABEL_AUTO_ID = "604"
APPLY_BUTTON_AUTO_ID = "1021"
APPLY_CHANGES_DIALOG_TITLE = "Apply Changes"
APPLY_CHANGES_YES_AUTO_ID = "6"

GAMMA_DISPLAY_MIN = 0.30
GAMMA_DISPLAY_MAX = 2.80

PROFILE_PATH = Path(__file__).with_name("profile.json")


def load_profile(name):
    profiles = json.loads(PROFILE_PATH.read_text())
    if name not in profiles:
        raise KeyError(f"No '{name}' profile in {PROFILE_PATH.name} (available: {list(profiles)})")
    return profiles[name]


def gamma_value_to_slider(gamma_value):
    fraction = (gamma_value - GAMMA_DISPLAY_MIN) / (GAMMA_DISPLAY_MAX - GAMMA_DISPLAY_MIN)
    return max(0.0, min(100.0, fraction * 100.0))


def set_gamma_precise(gamma_slider, gamma_label, target_value, tolerance=0.005, max_attempts=6):
    """
    Set the Gamma slider and verify against its own live label, correcting
    if needed. NVCP's own hint text warns that "very high and very low
    Brightness and Contrast values can limit the Gamma range", and in
    practice the slider's 0-100 -> display mapping can be briefly stale
    right after Brightness/Contrast change - so rather than trust a single
    static conversion, nudge the slider using the actual on-screen value
    until it matches the target.
    """
    guess = gamma_value_to_slider(target_value)
    current_value = None
    for _ in range(max_attempts):
        gamma_slider.set_value(max(0.0, min(100.0, guess)))
        time.sleep(0.3)
        try:
            current_value = float(gamma_label.window_text().strip())
        except ValueError:
            continue
        error = target_value - current_value
        if abs(error) <= tolerance:
            break
        guess += (error / (GAMMA_DISPLAY_MAX - GAMMA_DISPLAY_MIN)) * 100.0
    return current_value


def get_nvcp_aumid():
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "(Get-StartApps | Where-Object { $_.Name -eq 'NVIDIA Control Panel' }).AppID",
        ],
        capture_output=True, text=True, check=True,
    )
    aumid = result.stdout.strip()
    if not aumid:
        raise RuntimeError("Could not find an installed 'NVIDIA Control Panel' app on this machine")
    return aumid


def connect_to_nvcp_window(timeout=15):
    app = Application(backend="uia")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            app.connect(title=WINDOW_TITLE)
            return app
        except ElementNotFoundError:
            time.sleep(0.5)
    raise TimeoutError("NVIDIA Control Panel window did not appear in time")


def open_nvcp():
    try:
        return connect_to_nvcp_window(timeout=1)
    except TimeoutError:
        pass

    aumid = get_nvcp_aumid()
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
    return connect_to_nvcp_window(timeout=20)


def main():
    profile_name = sys.argv[1] if len(sys.argv) > 1 else "on"
    profile = load_profile(profile_name)
    brightness_percent = float(profile["brightness"])
    contrast_percent = float(profile["contrast"])
    gamma_value = float(profile["gamma"])

    app = open_nvcp()
    win = app.top_window()
    win.set_focus()

    win.child_window(title=COLOR_SETTINGS_TREE_ITEM, control_type="TreeItem").click_input()
    time.sleep(1)

    brightness_slider = win.child_window(auto_id=BRIGHTNESS_AUTO_ID, control_type="Slider")
    contrast_slider = win.child_window(auto_id=CONTRAST_AUTO_ID, control_type="Slider")
    gamma_slider = win.child_window(auto_id=GAMMA_AUTO_ID, control_type="Slider")
    gamma_label = win.child_window(auto_id=GAMMA_LABEL_AUTO_ID, control_type="Text")

    brightness_slider.set_value(brightness_percent)
    contrast_slider.set_value(contrast_percent)
    # Let NVCP settle on the new Brightness/Contrast before touching Gamma -
    # its valid range depends on them (see set_gamma_precise docstring).
    time.sleep(0.7)

    actual_gamma = set_gamma_precise(gamma_slider, gamma_label, gamma_value)
    if actual_gamma is None or abs(actual_gamma - gamma_value) > 0.01:
        print(f"Warning: Gamma landed on {actual_gamma} instead of {gamma_value:.2f} after retries.")

    try:
        apply_button = win.child_window(auto_id=APPLY_BUTTON_AUTO_ID, control_type="Button")
        apply_button.wait("exists enabled", timeout=3)
        apply_button.click_input()
        time.sleep(1)
        print("Clicked Apply.")
    except Exception:
        print("No pending changes to apply (values already matched).")

    print(
        f"Profile '{profile_name}': Brightness={brightness_percent}%, "
        f"Contrast={contrast_percent}%, Gamma={gamma_value:.2f}"
    )

    win.close()

    # Safety net: if the "Apply Changes" dialog shows up anyway, confirm it
    # rather than leaving the script hanging on a blocked close.
    try:
        dialog = win.child_window(title=APPLY_CHANGES_DIALOG_TITLE, control_type="Window")
        dialog.wait("exists", timeout=2)
        dialog.child_window(auto_id=APPLY_CHANGES_YES_AUTO_ID, control_type="Button").click_input()
    except Exception:
        pass


if __name__ == "__main__":
    main()
