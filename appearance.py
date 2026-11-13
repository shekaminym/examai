"""Theme palette, Aurora stylesheet mapping and optional Windows backdrop."""
import ctypes
import re
import sys

from PySide6.QtGui import QColor

THEMES = {
    "midnight": ("Midnight Black", "#141518", "#1c1d22", "#34363e", "#f4f4f8", "#bfc2ce", "#8e93ff"),
    "graphite": ("Graphite", "#26282a", "#313437", "#52565b", "#f6f7f8", "#c9cdd1", "#98aabc"),
    "liquid_blue": ("Liquid Blue", "#102454", "#203a72", "#526fac", "#f0f5ff", "#b9c8eb", "#8a9cff"),
    "frosted_light": ("Frosted Light", "#eef2f8", "#fafbff", "#bac5d8", "#192337", "#475772", "#495fc2"),
    "custom": ("Custom Accent", "#171820", "#222430", "#3c4052", "#f4f5ff", "#bfc4da", "#8e93ff"),
}


def valid_accent(value):
    return bool(re.fullmatch(r"#[\da-fA-F]{6}", value))


def blend(left, right, weight):
    a, b = QColor(left), QColor(right)
    return QColor(*(round(x + (y - x) * weight)
                    for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))).name()


def stylesheet(template, theme, custom):
    if theme == "liquid_blue":
        return template
    _, base, surface, border, text, muted, accent = THEMES.get(theme, THEMES["midnight"])
    if theme == "custom" and valid_accent(custom):
        accent = custom
    is_light = theme == "frosted_light"
    colors = {
        "0a173b": base, "192f67": blend(base, text, .055), "102454": base, "0b1b42": base,
        "f7f9ff": text, "f0f5ff": text, "f1f5ff": text, "e7efff": text, "e8efff": text,
        "e5ebff": text, "c9d8f7": text, "b7c7eb": muted, "b9c7eb": muted,
        "b9c8eb": muted, "b6c7ec": muted, "b8d2ff": muted, "d8c7ff": text,
        "a8f1d0": text, "d5ffe9": text, "ffb8c9": text,
    }
    for color in ("263a70", "1d356b", "142854", "1b315f", "172b56", "203a72",
                  "1b3268", "263e73", "203766", "2a447c", "21486d", "213767"):
        colors[color] = surface
    for color in ("526fac", "526b9e", "39558f", "526fa8", "5a73a6", "5b75a8", "67a7cf",
                  "607bb7", "8ba8e2"):
        colors[color] = border
    for color in ("96b7ff", "8eacf0", "668ee2", "a1acff", "92b2f1", "7089ed"):
        colors[color] = accent
    for color in ("5d70e8", "6079e6", "3c5cb6", "2c8ff2", "5378e8", "2e5b9f", "4d72d4",
                  "5478d2", "4b69b8", "4aa7ff", "6e8df5", "9b62ea", "8a5de4", "294783",
                  "a45add", "b674ee", "425b9b"):
        colors[color] = blend(accent, "#000000", .52 if is_light else .48)
    colors["2b467f"] = colors["35518b"] = blend(surface, accent, .15)
    return re.sub(r"#([0-9a-fA-F]{6})\b", lambda match: colors.get(
        match[1].lower(), match[0]), template)


def set_windows_backdrop(window, enabled):
    """Use the native DWM transient-window backdrop when Windows exposes it."""
    if sys.platform != "win32" or sys.getwindowsversion().build < 22621:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        backdrop = ctypes.c_int(3 if enabled else 1)
        return dwm.DwmSetWindowAttribute(ctypes.c_void_p(int(window.winId())), 38,
                                         ctypes.byref(backdrop), ctypes.sizeof(backdrop)) == 0 and enabled
    except (AttributeError, OSError):
        return False
