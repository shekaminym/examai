"""One policy for EXAM AI transitions and Qt's built-in popup effects."""

import ctypes
import sys

from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtWidgets import QApplication


def system_reduce_motion():
    """Respect Windows' client-area animation accessibility setting."""
    if sys.platform != "win32":
        return False
    enabled = ctypes.c_int(1)
    try:
        call = ctypes.windll.user32.SystemParametersInfoW
        call.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        if call(0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    except (AttributeError, OSError, TypeError):
        pass
    return False


class MotionManager:
    EFFECTS = (
        Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu,
        Qt.UIEffect.UI_AnimateCombo, Qt.UIEffect.UI_AnimateTooltip,
        Qt.UIEffect.UI_FadeTooltip, Qt.UIEffect.UI_AnimateToolBox,
    )

    def __init__(self, app=None, *, system_reduced=None):
        self.app = app or QApplication.instance()
        self.system_reduced = (system_reduce_motion() if system_reduced is None
                               else bool(system_reduced))
        self.original_effects = {
            effect: self.app.isEffectEnabled(effect) for effect in self.EFFECTS
        }
        self.animations = []
        self.level = "full"

    def register(self, *animations):
        self.animations.extend(animation for animation in animations
                               if isinstance(animation, QAbstractAnimation)
                               and animation not in self.animations)

    def configure(self, *, profile, intensity, reduce_motion):
        if self.system_reduced or reduce_motion or profile == "eco" or intensity == "off":
            level = "off"
        elif profile == "balanced" or intensity == "subtle":
            level = "reduced"
        else:
            level = "full"
        if level != self.level:
            self.level = level
            for effect, original in self.original_effects.items():
                self.app.setEffectEnabled(effect, original if level != "off" else False)
        if level == "off":
            for animation in self.animations:
                if animation.state() == QAbstractAnimation.State.Running:
                    animation.stop()
        return level

    def duration(self, base):
        return 0 if self.level == "off" else round(base * .7) if self.level == "reduced" else base

    def restore(self, *_args):
        for effect, original in self.original_effects.items():
            self.app.setEffectEnabled(effect, original)
