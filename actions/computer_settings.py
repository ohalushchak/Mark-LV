#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

from core import confirm
from core.undo import push_undo

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = _get_base_dir() / "config" / "api_keys.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def volume_up():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumeup")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True)

def volume_down():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumedown")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True)

def volume_mute():
    if _OS == "Windows":
        pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)

def _endpoint_volume():
    """The default output's IAudioEndpointVolume. pycaw 2025+ wraps the
    device and hands it over as `.EndpointVolume`; older versions return the
    raw COM device, which is activated here. On the newer pycaw the old code
    raised on every call — volume_get answered None and volume_set fell back
    to pressing mute twice while reporting the volume as set."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    device = AudioUtilities.GetSpeakers()
    ev = getattr(device, "EndpointVolume", None)
    if ev is not None:
        return ev
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def mute_get() -> bool | None:
    """Whether the output is muted, or None where it cannot be read."""
    try:
        if _OS == "Windows":
            return bool(_endpoint_volume().GetMute())
        if _OS == "Darwin":
            r = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() == "true"
        r = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                           capture_output=True, text=True, timeout=5)
        return "yes" in r.stdout.lower() if r.returncode == 0 else None
    except Exception:
        return None


def mute_set(on: bool) -> None:
    """Mute or unmute — not toggle. "Mute" used to press the mute key, which
    un-muted a machine that was already muted."""
    try:
        if _OS == "Windows":
            _endpoint_volume().SetMute(1 if on else 0, None)
            return
        if _OS == "Darwin":
            subprocess.run(["osascript", "-e",
                            f"set volume {'with' if on else 'without'} output muted"],
                           capture_output=True, timeout=5)
            return
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if on else "0"],
                       capture_output=True, timeout=5)
    except Exception as e:
        print(f"[Settings] mute_set failed, using the mute key: {e}")
        if mute_get() is not on:
            volume_mute()


def volume_get() -> int | None:
    """Current master volume 0-100, or None if this platform will not say.

    Undo needs a "before" value, and reading one is cheap on every OS we
    support. Where it is not readable the action simply is not registered as
    undoable — a wrong undo is worse than no undo."""
    try:
        if _OS == "Windows":
            # The scalar is the 0-100 the Windows volume slider shows; the old
            # dB-to-percent formula read 40% on the slider as ~4%.
            return max(0, min(100, round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)))
        if _OS == "Darwin":
            r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                               capture_output=True, text=True, timeout=5)
            return max(0, min(100, int(r.stdout.strip())))
        r = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)%", r.stdout)
        return max(0, min(100, int(m.group(1)))) if m else None
    except Exception:
        return None


def brightness_get() -> int | None:
    """Current brightness 0-100, or None where it cannot be read."""
    try:
        if _OS == "Windows":
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness)"
                 ".CurrentBrightness"],
                capture_output=True, text=True, timeout=5, **_WIN_HIDE
            )
            return max(0, min(100, int(r.stdout.strip())))
        if _OS == "Linux" and subprocess.run(
                ["which", "brightnessctl"], capture_output=True).returncode == 0:
            cur = int(subprocess.run(["brightnessctl", "get"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            mx  = int(subprocess.run(["brightnessctl", "max"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            return max(0, min(100, round(cur * 100 / mx))) if mx else None
    except Exception:
        pass
    return None


def brightness_set(value: int) -> None:
    """Set brightness to an absolute percentage. Only used to restore a value
    captured before a change, so it is undo's counterpart to the up/down pair."""
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
             f".WmiSetBrightness(1, {value})"],
            capture_output=True, timeout=5, **_WIN_HIDE
        )
    elif _OS == "Linux":
        subprocess.run(["brightnessctl", "set", f"{value}%"], capture_output=True)


def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        # No keypress fallback: there is no key for "set to N%", and the old
        # one (mute twice) changed nothing while the reply said it had. The
        # error reaches the reply, and the verdict reads the volume back.
        _endpoint_volume().SetMasterVolumeLevelScalar(value / 100.0, None)
        return
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")

def close_app(name: str = "") -> str:
    """Close an app by name, or the one in front; see actions/_app_closer.py.
    This used to press Alt+F4 / Cmd+Q in whatever window had focus and report
    success either way."""
    from actions._app_closer import close_app as _close, describe, reopen
    msg, closed = _close(name)
    if closed and any(w.path for w in closed):
        push_undo(f"closed {describe(closed)}", lambda c=closed: reopen(c))
    return msg

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def _show_foreground(cmd: int) -> bool:
    """ShowWindow on the window in front; False if there is none. Win+Down
    only restores a maximised window (seen live: "minimize" after "maximize"
    left it on screen), so Windows gets the call that does what it says."""
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return False
    u.ShowWindow(hwnd, cmd)
    return True


def minimize_window():
    if _OS == "Darwin":    pyautogui.hotkey("command", "m")
    elif _OS == "Windows":
        if not _show_foreground(6):                  # SW_MINIMIZE
            pyautogui.hotkey("win", "down")
    else:                  pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        if not _show_foreground(3):                  # SW_MAXIMIZE
            pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "left")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "right")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                lambda: mute_set(True),
    "unmute":              lambda: mute_set(False),
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

# ── What needs a human, and what just needs an undo ──────────────────────────
#
# The old gate was `_DANGEROUS_ACTIONS = {"restart", "shutdown"}` checked against
# a `confirmed` parameter the MODEL filled in — so the model confirmed its own
# shutdowns, and everything else (switching off the WiFi this assistant is
# talking over) had no gate at all.
#
# Two lists now, and the split is about reversibility, not about how alarming
# the word sounds:
#
#   _IRREVERSIBLE — a human presses a button on the HUD. Nothing else.
#   everything else — done immediately, with an undo pushed if it can be undone.
#
# Asking before every action is what makes an assistant unusable, and every
# question costs a round trip. Undo is both faster and safer than a prompt.
_IRREVERSIBLE = {
    "restart":     ("Restart this computer",
                    "Anything unsaved will be lost. The computer restarts in 10 seconds."),
    "shutdown":    ("Shut this computer down",
                    "Anything unsaved will be lost. The computer powers off in 10 seconds."),
    # Not obviously destructive, and that is exactly why it was missed: turning
    # the WiFi off cuts the assistant's own connection to the Live API, so it
    # cannot be asked to turn it back on.
    "toggle_wifi": ("Switch WiFi off or on",
                    "If this switches WiFi off, JARVIS loses its connection and "
                    "cannot switch it back on by voice."),
}

# Kept so anything still importing the old name keeps working.
_DANGEROUS_ACTIONS = set(_IRREVERSIBLE)


# ── Local intent resolution ──────────────────────────────────────────────────
#
# This used to be an entire extra Gemini call, made INSIDE the tool: the Live
# model called computer_settings, and computer_settings then asked a second
# model which action was meant. Every "turn the volume down" paid for two round
# trips — and when that second call failed, the fallback was
# `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it
# down" into `sesi_kis`, i.e. straight to "Unknown action".
#
# Nothing here needs a language model. The Live model already understands the
# sentence; it only needed the vocabulary, which the tool declaration now spells
# out in full. What is left is spelling tolerance, and difflib does that in
# microseconds instead of ~600 ms and a quota unit.
_ALIASES = {
    "volume_up":       ("louder", "raise volume", "turn it up", "increase volume"),
    "volume_down":     ("quieter", "lower volume", "turn it down", "decrease volume"),
    "mute":            ("silence", "sound off", "no sound"),
    "brightness_up":   ("brighter", "raise brightness", "increase brightness"),
    "brightness_down": ("dimmer", "dim", "lower brightness", "decrease brightness"),
    "close_window":    ("close this", "close it"),
    "full_screen":     ("fullscreen", "maximise screen"),
    "show_desktop":    ("minimise everything", "go to desktop"),
    "lock_screen":     ("lock", "lock the pc", "lock computer"),
    "sleep_display":   ("screen off", "turn off the screen", "display off"),
    "dark_mode":       ("night mode", "light mode", "toggle theme"),
    "toggle_wifi":     ("wifi", "wi-fi", "internet off", "internet on"),
    "task_manager":    ("processes", "task list"),
    "screenshot":      ("capture screen", "take a screenshot", "snip"),
    "refresh_page":    ("refresh", "reload page"),
    "new_tab":         ("open a tab", "open new tab"),
    "shutdown":        ("power off", "turn off the computer", "switch off the pc"),
    "restart":         ("reboot", "restart the pc"),
}

_VALUE_ACTIONS = {"volume_set", "type_text", "press_key", "reload_n",
                  "scroll_up", "scroll_down"}


def _normalise(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _detect_action(description: str) -> dict:
    """Resolve a free-text description to an action name, locally.

    Returns {"action": name, "value": ...}; `action` is "" when nothing matched,
    which the caller turns into a message naming real candidates — one round
    trip, and only in the case that used to cost one anyway."""
    raw  = (description or "").strip()
    norm = _normalise(raw)
    if not norm:
        return {"action": "", "value": None}

    known = set(ACTION_MAP) | _VALUE_ACTIONS

    # 1. Already an action name.
    if norm in known:
        return {"action": norm, "value": None}

    low = raw.lower()

    # 2. "set volume to 30", "sesi 30 yap" — a number next to a volume word.
    num = re.search(r"(\d{1,3})\s*%?", low)
    if num and any(w in low for w in ("volume", "ses", "sound", "lautstark", "громкость")):
        return {"action": "volume_set", "value": max(0, min(100, int(num.group(1))))}

    # 3. "close chrome", "quit task manager" — closing an app by name. Not
    #    "close this", "close the tab": those act on what is in front.
    m = re.match(r"^(?:close|quit|exit|закрий|закрити)\s+(?:the\s+)?(.+)$", low)
    if m and m.group(1).strip() not in ("this", "it", "tab", "window", "the tab",
                                        "the window", "this tab", "this window"):
        return {"action": "close_app", "value": m.group(1).strip()}

    # 3b. Alias phrases.
    for action, phrases in _ALIASES.items():
        if any(_normalise(p) == norm or p in low for p in phrases):
            return {"action": action, "value": None}

    # 4. Fuzzy match on the action names — catches "fullscren", "volumeup".
    import difflib
    close = difflib.get_close_matches(norm, sorted(known), n=1, cutoff=0.72)
    if close:
        return {"action": close[0], "value": None}

    # 5. Substring: "increase_the_brightness" contains "brightness".
    for action in sorted(known, key=len, reverse=True):
        if len(action) > 4 and (action in norm or norm in action):
            return {"action": action, "value": None}

    return {"action": "", "value": None}


def _suggest(description: str) -> str:
    """What to tell the model when nothing matched. Names real actions so its
    retry lands, instead of the old 'Unknown action' dead end."""
    import difflib
    near = difflib.get_close_matches(_normalise(description),
                                     sorted(ACTION_MAP), n=5, cutoff=0.3)
    hint = ", ".join(near) if near else ", ".join(sorted(ACTION_MAP)[:12])
    return (f"I could not match '{description}' to a computer action. "
            f"Call computer_settings again with an exact `action` from: {hint}.")

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return _suggest(description or raw_action)

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    # ── The gate ─────────────────────────────────────────────────────────────
    # A human presses a button, or this does not happen. The model can no longer
    # write its own permission slip, and the action itself is handed to the UI
    # rather than performed here — so returning early is not "declining", it is
    # "parked until someone says yes".
    if action in _IRREVERSIBLE:
        title, detail = _IRREVERSIBLE[action]
        func = ACTION_MAP.get(action)
        if func is None:
            return f"Unknown action: '{raw_action}'."
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key=action, title=title, detail=detail,
            run=lambda f=func, a=action: (f(), f"{a} done.")[1],
        )

    if action == "volume_set":
        try:
            target = int(value if value is not None else 50)
            before = volume_get()
            volume_set(target)
            if before is not None:
                push_undo(f"volume {before}% → {target}%",
                          lambda b=before: (volume_set(b), f"Back to {b}%.")[1])
            return f"Volume set to {target}%."
        except Exception as e:
            return f"Could not set volume: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=False)
        if enter_after:
            from actions.computer_control import send_guard
            held = send_guard(params, "enter", press_enter)
            if held:
                return f"Typed: {text[:80]}. {held}"
            time.sleep(0.1)
            press_enter()
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        from actions.computer_control import send_guard
        held = send_guard(params, key, lambda: press_key(key))
        if held:
            return held
        press_key(key)
        return f"Pressed: {key}"

    if action == "enter":
        from actions.computer_control import send_guard
        held = send_guard(params, "enter", press_enter)
        if held:
            return held

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action in ("close_app", "quit_app", "close_application"):
        name = str(value or params.get("app", "") or "").strip()
        return close_app(name)

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return _suggest(raw_action or description)

    # ── The window the user named ────────────────────────────────────────────
    # Window actions work on the window in front. Seen live: "minimise
    # Outlook" minimised Claude, which was in front — and the check honestly
    # reported "'Claude' is minimised". A named window is brought forward first.
    target = _window_target(action, value)
    if target:
        if _OS != "Windows":
            return f"I can only pick a window by name on Windows; {action} works on the one in front."
        from actions.computer_control import _focus_window_win
        msg = _focus_window_win(target)
        if msg.startswith("Could not"):
            return msg
        if action == "switch_window":
            return msg

    # ── Capture "before" so the change can be taken back ─────────────────────
    # Read-then-write is the whole mechanism for settings: there is no clever
    # inverse to compute, just a value to remember. Where the platform will not
    # tell us the current value, nothing is registered — an undo that restores
    # a guess is worse than no undo at all.
    _before = None
    if action in ("volume_up", "volume_down", "mute", "unmute", "toggle_mute"):
        _before = ("volume", volume_get())
    elif action in ("brightness_up", "brightness_down"):
        _before = ("brightness", brightness_get())

    try:
        func()
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"

    if _before:
        kind, old = _before
        if old is not None:
            if kind == "volume":
                push_undo(f"volume ({action})",
                          lambda b=old: (volume_set(b), f"Volume back to {b}%.")[1])
            elif kind == "brightness":
                push_undo(f"brightness ({action})",
                          lambda b=old: (brightness_set(b), f"Brightness back to {b}%.")[1])
    elif action == "dark_mode":
        # A pure toggle: calling it again is the undo.
        push_undo("dark mode toggled",
                  lambda: (dark_mode(), "Theme switched back.")[1])

    return f"Done: {action}."


# ── Did it take effect? (core/verify.py) ─────────────────────────────────────
#
# Everything above ended in "Done: {action}." — including volume_set, whose
# pycaw failure fell back to pressing mute twice, and "mute", which pressed the
# mute key and so un-muted a muted machine. Each setting is now read back:
# volume, mute, brightness and theme by value; window actions by the state of
# the window that was in front; shortcuts by what changed on screen (the same
# check as computer_control). A value the system will not report is
# UNVERIFIED. (bfrpaulondev/Mark-BP's settings and window-state checks.)

_VOLUME = {"volume_up", "volume_down", "volume_set", "mute", "unmute", "toggle_mute"}
_BRIGHTNESS = {"brightness_up", "brightness_down"}
_WINDOW = {"minimize", "maximize", "full_screen", "fullscreen", "snap_left", "snap_right",
           "switch_window", "show_desktop", "close_window"}
_OPENERS = {"task_manager", "file_explorer", "open_settings", "open_run", "screenshot"}
_SCREEN = {"refresh_page", "reload", "reload_n", "refresh_n", "reload_page_n", "close_tab",
           "new_tab", "next_tab", "prev_tab", "go_back", "go_forward", "zoom_in", "zoom_out",
           "zoom_reset", "find_on_page", "scroll_up", "scroll_down", "scroll_top",
           "scroll_bottom", "page_up", "page_down", "paste", "cut", "undo", "redo",
           "select_all", "save", "enter", "escape", "pause_video", "play_pause",
           "focus_search", "press_key"}


def _resolved(params: dict) -> tuple[str, object]:
    """The action and value computer_settings() will act on — the same
    resolution as the top of the handler."""
    params = params or {}
    raw = str(params.get("action", "") or "").strip()
    value = params.get("value", None)
    if not raw and params.get("description"):
        d = _detect_action(str(params.get("description")))
        raw = d.get("action", "")
        if value is None:
            value = d.get("value")
    return raw.lower().strip().replace(" ", "_").replace("-", "_"), value


def theme_is_light() -> bool | None:
    try:
        if _OS == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
        if _OS == "Darwin":
            r = subprocess.run(["osascript", "-e", 'tell app "System Events" to tell '
                                'appearance preferences to get dark mode'],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() != "true"
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                           capture_output=True, text=True, timeout=5)
        return "dark" not in r.stdout
    except Exception:
        return None


_TARGETABLE = {"minimize", "maximize", "full_screen", "fullscreen", "snap_left", "snap_right",
               "switch_window"}


def _window_target(action: str, value) -> str:
    """The window name in `value` for a window action, or ""."""
    if action not in _TARGETABLE or value is None:
        return ""
    v = str(value).strip()
    return "" if not v or v.isdigit() else v


def _snapshot(params: dict):
    from core import desktop_state as ds
    action, value = _resolved(params)
    target = _window_target(action, value)
    if target and _OS == "Windows":
        from actions.computer_control import _find_window_win
        w = _find_window_win(target)
        return {"fg": ds.window(w.hwnd) if w else None, "target": target}
    if action in _VOLUME:
        return {"volume": volume_get(), "muted": mute_get()}
    if action in _BRIGHTNESS:
        return {"brightness": brightness_get()}
    if action == "dark_mode":
        return {"light": theme_is_light()}
    if action in _WINDOW:
        return {"fg": ds.foreground()}
    if action in _OPENERS:
        return {"fg": ds.foreground(), "windows": {w.hwnd for w in ds.windows()}}
    if action == "copy":
        try:
            return {"clip": pyperclip.paste() if _PYPERCLIP else None}
        except Exception:
            return {"clip": None}
    if action in ("type_text", "write_on_screen", "type", "write"):
        return {"focus": ds.focused_control()}
    if action in _SCREEN:
        from actions.computer_control import _snapshot as cc_snapshot
        return cc_snapshot({"action": "press"})
    return None


def _verify_volume(action, value, before):
    from core import verify as v
    b, bm = before.get("volume"), before.get("muted")
    if action in ("mute", "unmute", "toggle_mute"):
        def look():
            m = mute_get()
            if m is None:
                return v.unverified("this system does not report whether it is muted")
            want = {"mute": True, "unmute": False}.get(action, None if bm is None else not bm)
            if want is None:
                return v.unverified(f"the sound is now {'muted' if m else 'on'}")
            if m == want:
                return v.verified(f"the sound is {'muted' if m else 'on'}")
            return v.failed(f"the sound is still {'muted' if m else 'on'}")
        return v.poll(look)

    def look():
        a = volume_get()
        if a is None or b is None:
            return v.unverified("this system does not report its volume")
        if action == "volume_set":
            target = max(0, min(100, int(value if value is not None else 50)))
            return (v.verified(f"the volume is {a}%") if abs(a - target) <= 2
                    else v.failed(f"the volume is {a}%, not {target}%"))
        up = action == "volume_up"
        if (a > b) if up else (a < b):
            return v.verified(f"the volume is {a}% (it was {b}%)")
        if a == (100 if up else 0):
            return v.verified(f"the volume is already at {a}%")
        return v.failed(f"the volume stayed at {a}%")
    return v.poll(look)


def _verify_window(action, before):
    from core import desktop_state as ds
    from core import verify as v
    if _OS != "Windows":
        return v.unverified("I can only read window state on Windows")
    fg0 = before.get("fg")
    if not fg0:
        return v.unverified("I could not tell which window was in front before")
    name = f"'{fg0['title'][:50]}'"

    def look():
        w, fg = ds.window(fg0["hwnd"]), ds.foreground() or {}
        if action == "switch_window" and before.get("target"):
            return (v.verified(f"{name} is in front now") if fg.get("hwnd") == fg0["hwnd"]
                    else v.failed(f"'{fg.get('title', '')[:50]}' is in front, not {name}"))
        if action == "switch_window":
            return (v.verified(f"'{fg.get('title', '')[:50]}' is in front now")
                    if fg.get("hwnd") and fg["hwnd"] != fg0["hwnd"]
                    else v.failed(f"{name} is still in front"))
        if action == "close_window":
            if w is None or not w["visible"]:
                return v.verified(f"{name} closed")
            if w["title"] != fg0["title"]:
                return v.verified(f"the tab closed; the window now shows '{w['title'][:50]}'")
            return v.unverified(f"{name} is still open with the same title")
        if w is None:
            return v.unverified(f"{name} is gone")
        if action in ("minimize", "show_desktop"):
            return (v.verified(f"{name} is minimised") if w["minimized"]
                    else v.failed(f"{name} is still on screen"))
        if action == "maximize":
            return (v.verified(f"{name} is maximised") if w["maximized"]
                    else v.failed(f"{name} is not maximised"))
        if w["rect"] != fg0["rect"]:
            return v.verified(f"{name} moved or resized")
        if action in ("full_screen", "fullscreen"):
            return v.unverified(f"{name} did not change size — the app may not have a full-screen mode")
        return v.failed(f"{name} did not move")
    return v.poll(look)


def _verify(params: dict, result, before):
    from core import desktop_state as ds
    from core import verify as v
    if before is None or str(result or "").startswith("[CONFIRMATION_PENDING]"):
        return None
    action, value = _resolved(params)

    if action in _VOLUME:
        return _verify_volume(action, value, before)

    if action in _BRIGHTNESS:
        b = before.get("brightness")

        def look():
            a = brightness_get()
            if a is None or b is None:
                return v.unverified("this display does not report its brightness")
            up = action == "brightness_up"
            if (a > b) if up else (a < b):
                return v.verified(f"the brightness is {a}% (it was {b}%)")
            if a == (100 if up else 0):
                return v.verified(f"the brightness is already at {a}%")
            return v.failed(f"the brightness stayed at {a}%")
        return v.poll(look)

    if action == "dark_mode":
        b = before.get("light")

        def look():
            a = theme_is_light()
            if a is None or b is None:
                return v.unverified("I could not read the theme setting")
            if a != b:
                return v.verified(f"apps now use the {'light' if a else 'dark'} theme")
            return v.failed(f"apps still use the {'light' if a else 'dark'} theme")
        return v.poll(look)

    if action in _WINDOW:
        return _verify_window(action, before)

    if action in _OPENERS:
        if _OS != "Windows":
            return v.unverified("I can only see new windows on Windows")

        def look():
            fg = ds.foreground() or {}
            fg0 = before.get("fg") or {}
            if fg.get("hwnd") and fg["hwnd"] != fg0.get("hwnd"):
                return v.verified(f"'{fg['title'][:50] or fg['exe']}' opened in front")
            new = [w for w in ds.windows() if w.hwnd not in before["windows"]]
            if new:
                return v.verified(f"'{new[0].title[:50]}' opened")
            return v.unverified("no new window appeared in front")
        return v.poll(look, timeout=3.0)

    if action == "copy":
        def look():
            try:
                now = pyperclip.paste() if _PYPERCLIP else None
            except Exception:
                now = None
            if now is None:
                return v.unverified("I could not read the clipboard")
            if now != before.get("clip"):
                return v.verified(f"the clipboard now holds {len(now)} characters")
            return v.unverified("the clipboard did not change — maybe nothing was selected")
        return v.poll(look)

    if action in ("type_text", "write_on_screen", "type", "write"):
        from actions.computer_control import _verify_typing
        text = str(value or (params or {}).get("text", "")).strip()
        return _verify_typing("type", {"text": text}, before)

    if action in _SCREEN:
        from actions.computer_control import _verify as cc_verify
        key = "enter" if action == "enter" else str(value or (params or {}).get("key", ""))
        return cc_verify({"action": "press", "key": key}, result, before)

    return None


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_settings",
    "description": "Controls the computer: volume, brightness, window management, keyboard shortcuts, typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. Use for ANY single computer control command. restart, shutdown and toggle_wifi put a confirmation on the user's screen and do NOT happen until they press it — never claim they are done. Volume, brightness and dark mode can be reversed with the `undo` tool. close_app takes the app's name in `value` (e.g. 'Task Manager', 'chrome' — in English, or the window title) and reports whether it really closed; repeat its reason to the user and never fall back to pressing Alt+F4, which closes whatever window happens to have focus.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            # The exact vocabulary, spelled out.
            #
            # This used to say only "The action to perform", so the model
            # usually filled `description` instead — and computer_settings then
            # made a SECOND Gemini call, inside the tool, purely to translate
            # that sentence into one of these names. Every "turn the volume
            # down" cost two model round trips.
            #
            # Naming the actions here costs ~600 characters once per session and
            # removes a whole round trip from every computer command.
            "action": {
                "type": "STRING",
                "description": (
                    "The exact action. Prefer this over `description` — pick one of: "
                    "volume_up | volume_down | volume_set | mute | "
                    "brightness_up | brightness_down | sleep_display | "
                    "pause_video | close_app | close_window | full_screen | "
                    "minimize | maximize | snap_left | snap_right | "
                    "switch_window | show_desktop | task_manager | focus_search | "
                    "refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                    "go_back | go_forward | zoom_in | zoom_out | zoom_reset | "
                    "find_on_page | scroll_up | scroll_down | scroll_top | "
                    "scroll_bottom | page_up | page_down | copy | paste | cut | "
                    "undo | redo | select_all | save | enter | escape | press_key | "
                    "type_text | screenshot | lock_screen | open_settings | "
                    "file_explorer | open_run | dark_mode | toggle_wifi | "
                    "restart | shutdown"
                )
            },
            "description": {
                "type": "STRING",
                "description": (
                    "Fallback only, when no action name above fits. "
                    "Resolved locally — no extra model call."
                )
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": "Only on the follow-up call after a [CONFIRM_SEND] result, once the user has said yes."
            },
            "value": {
                "type": "STRING",
                "description": "Optional value: volume level 0-100, text to type, key name, the app to close for close_app, or for minimize / maximize / full_screen / snap_left / snap_right / switch_window the app or window to act on (e.g. 'Outlook') — without it they act on the window in front."
            }
        },
        "required": []
    },
    "handler": computer_settings,
    "snapshot": _snapshot,
    "verify": _verify,
}
