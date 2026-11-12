"""Keep capture affinity attached to the current Qt native window handles."""
from __future__ import annotations

from weakref import WeakKeyDictionary, WeakSet
import sys

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from privacy_utils import (
    AffinityResult, WDA_EXCLUDEFROMCAPTURE, WDA_NONE,
    get_capture_exclusion, set_capture_exclusion,
)


class CapturePrivacy(QObject):
    state_changed = Signal(bool, str)

    def __init__(self, app: QApplication, parent=None, *, apply_api=None):
        super().__init__(parent)
        self.app = app
        self.requested = False
        self.verified = False
        self.message = ""
        self._apply_api = apply_api or set_capture_exclusion
        self._windows = WeakSet()
        self._required = WeakSet()
        self._retired = WeakSet()
        self._results = WeakKeyDictionary()
        self._busy = False
        self._last_state = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(150)
        self._settle_timer.timeout.connect(self._refresh)
        app.installEventFilter(self)

    def register(self, window: QWidget) -> None:
        """Call after window construction and before its first show()."""
        self._retired.discard(window)
        self._track(window)
        self._required.add(window)
        if self.requested:
            self._apply_one(window)
            self._publish()

    def unregister(self, window: QWidget) -> None:
        """Retire a finished splash without touching its closing native HWND.

        Hide/WinIdChange/destruction notifications and the next ON/OFF rescan
        must not bring this retired window back into the protected set.
        """
        self._retired.add(window)
        self._windows.discard(window)
        self._required.discard(window)
        self._results.pop(window, None)
        self._publish()

    def set_enabled(self, enabled: bool) -> bool:
        self.requested = bool(enabled)
        # Include popups that have already acquired their own HWNDs.
        for window in self.app.topLevelWidgets():
            if window.windowHandle() is not None:
                self._track(window)
        for window in list(self._windows):
            if isValid(window):
                self._apply_one(window)
        self._publish()
        self._settle_timer.start()
        return self.verified if enabled else not self.message

    def _track(self, window: QWidget) -> None:
        if window in self._retired:
            return
        if window not in self._windows:
            self._windows.add(window)
            window.destroyed.connect(self._schedule_refresh)

    def _schedule_refresh(self, *_args) -> None:
        self._refresh_timer.start(0)

    def diagnostics(self) -> str:
        """Whitelist window/OS facts; never include config, audio, text or keys."""
        lines = [
            "EXAM AI Ghost v2.1 — rounded/global opacity",
            f"requested={self.requested}; window_flags_confirmed={self.verified}",
            f"python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ]
        if sys.platform == "win32":
            version = sys.getwindowsversion()
            lines.append(f"Windows={version.major}.{version.minor}; build={version.build}")
        else:
            lines.append(f"platform={sys.platform}")
        for window in list(self._windows):
            if not isValid(window) or window.windowHandle() is None:
                continue
            if window not in self._required and not window.isVisible():
                continue
            # Read-only diagnostics must not create/recreate a hidden HWND.
            hwnd = int(window.effectiveWinId() or 0)
            if not hwnd:
                continue
            result = get_capture_exclusion(hwnd)
            affinity = f"0x{result.affinity:02x}" if result.success else result.message
            lines.append(
                f"{type(window).__name__}: visible={window.isVisible()}; "
                f"opacity={window.windowOpacity():.3f}; rounded={not window.mask().isEmpty()}; "
                f"affinity={affinity}"
            )
        lines.append("The Windows flag alone does not verify every conferencing capture path.")
        return "\n".join(lines)

    def _apply_one(self, window: QWidget) -> None:
        if not isValid(window) or window in self._retired:
            return
        self._busy = True
        try:
            # A QWidget can recreate its HWND after setWindowFlags(). Always
            # obtain the live handle here, never cache an old integer handle.
            result = self._apply_api(int(window.winId()), self.requested)
            self._results[window] = result
        except (OSError, ValueError, TypeError) as error:
            self._results[window] = AffinityResult(False, message=f"Capture exclusion failed: {error}")
        finally:
            self._busy = False

    def _refresh(self) -> None:
        for window in list(self._windows):
            if (isValid(window) and window.windowHandle() is not None
                    and (window in self._required or window.isVisible())):
                self._apply_one(window)
        self._publish()

    def _publish(self) -> None:
        # A hidden registered main/splash is checked too, before its first show.
        results = [self._results.get(window) for window in self._windows if (
            isValid(window) and (window.isVisible() or (window in self._required and (
                self.requested or window in self._results
            )))
        )]
        expected = WDA_EXCLUDEFROMCAPTURE if self.requested else WDA_NONE
        failures = [result for result in results if (
            result is None or not result.success or result.affinity != expected
        )]
        self.verified = bool(self.requested and results and not failures and all(
            result.affinity == WDA_EXCLUDEFROMCAPTURE for result in results
        ))
        self.message = ""
        if failures:
            first = failures[0]
            self.message = (first.message if first else "") or "Native window protection has not been verified."
        state = (self.verified, self.message)
        if state != self._last_state:
            self._last_state = state
            self.state_changed.emit(*state)

    def eventFilter(self, watched, event):
        if (self._busy or watched in self._retired
                or not isinstance(watched, QWidget) or not watched.isWindow()):
            return False
        event_type = event.type()
        if event_type not in (
            QEvent.Type.Show, QEvent.Type.Hide,
            QEvent.Type.WinIdChange, QEvent.Type.WindowStateChange,
        ):
            return False
        if not self.requested and watched not in self._windows:
            return False
        self._track(watched)
        if event_type == QEvent.Type.Hide:
            self._schedule_refresh()
            return False
        # WinIdChange also fires during teardown; do not recreate a destroyed
        # native window just to protect it. Show will apply before painting.
        if event_type == QEvent.Type.Show or watched.windowHandle() is not None:
            self._apply_one(watched)
        else:
            self._results.pop(watched, None)
        # Recheck after Qt has completed native style/handle changes. Deduplicate
        # the burst of native events without a polling timer or UI-thread loop.
        self._refresh_timer.start(0)
        self._settle_timer.start()
        return False
