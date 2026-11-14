"""Offline checks for verified Win32 capture affinity and Qt window lifecycle.

All Windows calls are mocked. Qt runs offscreen and never changes the user's
desktop, opens a microphone, loads a model, or sends a network request.
"""
from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from shiboken6 import isValid

from examai.windows.privacy_qt import CapturePrivacy
from examai.windows.privacy_utils import (
    AffinityResult,
    WDA_EXCLUDEFROMCAPTURE,
    WDA_MONITOR,
    WDA_NONE,
    WindowsCaptureAPI,
)


class WindowsCaptureAPITests(unittest.TestCase):
    def setUp(self):
        # Do not load user32 or call the native constructor during offline QA.
        self.api = WindowsCaptureAPI.__new__(WindowsCaptureAPI)
        self.api.available = True
        self.api.user32 = MagicMock()
        self.api.dwmapi = MagicMock()
        self.hwnd = 0x12345678ABCDEF01 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x1234ABCD
        self.observed_affinity = WDA_EXCLUDEFROMCAPTURE
        self.api.user32.IsWindow.return_value = 1
        self.api.user32.GetAncestor.return_value = self.hwnd
        self.api.user32.SetWindowDisplayAffinity.return_value = 1

        def own_process(_hwnd, process_pointer):
            process_pointer._obj.value = os.getpid()
            return 17

        def read_affinity(_hwnd, affinity_pointer):
            affinity_pointer._obj.value = self.observed_affinity
            return 1

        def composed(composition_pointer):
            composition_pointer._obj.value = 1
            return 0

        self.api.user32.GetWindowThreadProcessId.side_effect = own_process
        self.api.user32.GetWindowDisplayAffinity.side_effect = read_affinity
        self.api.dwmapi.DwmIsCompositionEnabled.side_effect = composed
        version = patch("examai.windows.privacy_utils.sys.getwindowsversion", create=True,
                        return_value=SimpleNamespace(major=10, build=22631))
        version.start()
        self.addCleanup(version.stop)
        # create=True also permits running the mocked suite outside Windows.
        set_error = patch("examai.windows.privacy_utils.ctypes.set_last_error", create=True)
        set_error.start()
        self.addCleanup(set_error.stop)

    def test_enable_passes_pointer_sized_hwnd_and_verifies_exact_exclusion_flag(self):
        result = self.api.apply(self.hwnd, True)
        self.assertTrue(result.success)
        self.assertTrue(result.excluded)
        sent_hwnd, sent_flag = self.api.user32.SetWindowDisplayAffinity.call_args.args
        self.assertEqual(sent_hwnd.value, self.hwnd)
        self.assertEqual(sent_flag, 0x11)
        read_hwnd, _ = self.api.user32.GetWindowDisplayAffinity.call_args.args
        self.assertEqual(read_hwnd.value, self.hwnd)
        self.api.user32.GetWindowDisplayAffinity.assert_called_once()

    def test_monitor_only_fallback_is_not_reported_as_enabled(self):
        self.observed_affinity = WDA_MONITOR
        result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertFalse(result.excluded)
        self.assertEqual(result.affinity, WDA_MONITOR)
        self.assertIn("not confirmed", result.message)

    def test_set_error_preserves_native_error_code_without_false_success(self):
        self.api.user32.SetWindowDisplayAffinity.return_value = 0
        with patch("examai.windows.privacy_utils.ctypes.get_last_error", create=True, return_value=87):
            result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, 87)
        self.assertIn("SetWindowDisplayAffinity", result.message)
        self.api.user32.GetWindowDisplayAffinity.assert_not_called()

    def test_read_error_does_not_claim_verified_protection(self):
        self.api.user32.GetWindowDisplayAffinity.side_effect = None
        self.api.user32.GetWindowDisplayAffinity.return_value = 0
        with patch("examai.windows.privacy_utils.ctypes.get_last_error", create=True, return_value=5):
            result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, 5)
        self.assertIn("GetWindowDisplayAffinity", result.message)

    def test_unavailable_platform_never_calls_windows_functions(self):
        self.api.available = False
        result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertIn("Windows", result.message)
        self.api.user32.IsWindow.assert_not_called()

    def test_unsupported_windows_build_rejects_instead_of_using_black_rectangle(self):
        with patch("examai.windows.privacy_utils.sys.getwindowsversion", create=True,
                   return_value=SimpleNamespace(major=10, build=18362)):
            result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertIn("19041", result.message)
        self.api.user32.SetWindowDisplayAffinity.assert_not_called()

    def test_unavailable_dwm_rejects_without_setting_affinity(self):
        self.api.dwmapi.DwmIsCompositionEnabled.side_effect = lambda _pointer: 0
        result = self.api.apply(self.hwnd, True)
        self.assertFalse(result.success)
        self.assertIn("composition", result.message)
        self.api.user32.SetWindowDisplayAffinity.assert_not_called()

    def test_off_clears_affinity_and_verifies_none(self):
        self.observed_affinity = WDA_NONE
        result = self.api.apply(self.hwnd, False)
        self.assertTrue(result.success)
        self.assertFalse(result.excluded)
        self.assertEqual(result.affinity, WDA_NONE)
        self.assertEqual(self.api.user32.SetWindowDisplayAffinity.call_args.args[1], 0)
        self.api.dwmapi.DwmIsCompositionEnabled.assert_not_called()

    def test_child_or_foreign_window_is_not_changed(self):
        self.api.user32.GetAncestor.return_value = self.hwnd + 1
        self.assertFalse(self.api.apply(self.hwnd, True).success)
        self.api.user32.SetWindowDisplayAffinity.assert_not_called()
        self.api.user32.GetAncestor.return_value = self.hwnd
        self.api.user32.GetWindowThreadProcessId.side_effect = lambda _hwnd, pointer: setattr(
            pointer._obj, "value", os.getpid() + 100
        )
        self.assertFalse(self.api.apply(self.hwnd, True).success)
        self.api.user32.SetWindowDisplayAffinity.assert_not_called()


class CapturePrivacyQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.api = MagicMock(side_effect=lambda _hwnd, enabled: AffinityResult(
            True, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        ))
        self.manager = CapturePrivacy(self.app, apply_api=self.api)
        self.windows = []

    def tearDown(self):
        self.app.removeEventFilter(self.manager)
        self.manager._refresh_timer.stop()
        for window in self.windows:
            if isValid(window):
                window.close()
                window.deleteLater()
        self.manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def window(self):
        widget = QWidget()
        self.windows.append(widget)
        return widget

    def test_main_and_splash_are_verified_before_first_show(self):
        main, splash = self.window(), self.window()
        self.manager.register(main)
        self.manager.register(splash)
        self.assertFalse(main.isVisible())
        self.assertFalse(splash.isVisible())
        self.assertTrue(self.manager.set_enabled(True))
        handles = {call.args[0] for call in self.api.call_args_list if call.args[1]}
        self.assertIn(int(main.winId()), handles)
        self.assertIn(int(splash.winId()), handles)
        self.assertTrue(self.manager.verified)

    def test_hidden_optional_settings_do_not_fail_toggle_but_are_checked_on_show(self):
        main, settings = self.window(), self.window()
        self.manager.register(main)
        self.manager.register(settings, require_hidden=False)
        self.assertTrue(self.manager.set_enabled(True))
        self.assertIsNone(settings.windowHandle())
        self.assertEqual(self.manager.message, "")
        settings.show()
        self.app.processEvents()
        self.api.assert_any_call(int(settings.winId()), True)
        self.assertTrue(self.manager.verified)

    def test_winid_change_waits_for_replacement_handle(self):
        window = self.window()
        self.manager.register(window)
        self.manager.set_enabled(True)
        self.api.reset_mock()
        QApplication.sendEvent(window, QEvent(QEvent.Type.WinIdChange))
        self.api.assert_not_called()
        self.app.processEvents()
        self.api.assert_any_call(int(window.winId()), True)

    def test_fresh_off_does_not_treat_uncreated_hidden_splash_as_failure(self):
        main, splash = self.window(), self.window()
        self.manager.register(main)
        self.manager.register(splash)
        # Startup may create the main native handle before the splash has one,
        # while Ghost Mode is still OFF. That is not a failed ON request.
        main_handle = int(main.winId())
        self.app.processEvents()
        self.assertIsNone(splash.windowHandle())
        self.assertFalse(self.manager.requested)
        self.assertFalse(self.manager.verified)
        self.assertEqual(self.manager.message, "")
        self.api.assert_any_call(main_handle, False)

    def test_diagnostics_only_include_window_facts_not_titles_text_or_credentials(self):
        window = self.window()
        private = {
            "title": "synthetic-private-window-title",
            "text": "synthetic-private-question-and-answer",
            "credential": "synthetic-private-credential-marker",
        }
        window.setWindowTitle(private["title"])
        QLabel(private["text"], window)
        window.setProperty("gemini_api_key", private["credential"])
        self.manager.register(window)
        self.manager.set_enabled(True)
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": private["credential"],
        }), patch("examai.windows.privacy_qt.get_capture_exclusion", return_value=AffinityResult(
            True, WDA_EXCLUDEFROMCAPTURE,
        )) as query:
            diagnostic = self.manager.diagnostics()
        query.assert_called_once_with(int(window.winId()))
        self.assertIn("EXAM AI Ghost v2", diagnostic)
        self.assertIn("QWidget:", diagnostic)
        self.assertIn("affinity=0x11", diagnostic)
        self.assertIn("window_flags_confirmed=True", diagnostic)
        for value in private.values():
            self.assertNotIn(value, diagnostic)
        self.assertNotIn("GEMINI_API_KEY", diagnostic)

    def test_restore_and_window_flag_changes_reapply_requested_protection(self):
        window = self.window()
        self.manager.register(window)
        self.manager.set_enabled(True)
        window.show()
        self.app.processEvents()
        self.api.reset_mock()
        window.showMinimized()
        self.app.processEvents()
        window.showNormal()
        self.app.processEvents()
        self.api.assert_any_call(int(window.winId()), True)
        self.assertTrue(self.manager.verified)
        # setWindowFlag can change the HWND on Windows. Check the actual handle
        # after Qt's flag change, without assuming it changes on every platform.
        self.api.reset_mock()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.show()
        self.app.processEvents()
        self.api.assert_any_call(int(window.winId()), True)
        self.assertTrue(self.manager.verified)

    def test_splash_registered_after_enabling_is_protected_immediately(self):
        main = self.window()
        self.manager.register(main)
        self.assertTrue(self.manager.set_enabled(True))
        self.api.reset_mock()
        splash = self.window()
        self.manager.register(splash)
        self.assertFalse(splash.isVisible())
        self.api.assert_any_call(int(splash.winId()), True)
        self.assertTrue(self.manager.verified)

    def test_show_and_winid_change_reapply_to_current_handle(self):
        window = self.window()
        self.manager.register(window)
        self.manager.set_enabled(True)
        self.api.reset_mock()
        # Offscreen QWidget.show() dispatches Show without touching the desktop.
        window.show()
        self.app.processEvents()
        self.api.assert_any_call(int(window.winId()), True)
        self.api.reset_mock()
        QApplication.sendEvent(window, QEvent(QEvent.Type.WinIdChange))
        self.app.processEvents()
        self.api.assert_any_call(int(window.winId()), True)
        self.assertTrue(self.manager.verified)

    def test_api_failure_never_emits_active_state(self):
        self.api.side_effect = lambda *_args: AffinityResult(False, message="Windows error 87", error_code=87)
        window = self.window()
        self.manager.register(window)
        states = []
        self.manager.state_changed.connect(lambda enabled, message: states.append((enabled, message)))
        self.assertFalse(self.manager.set_enabled(True))
        self.assertFalse(self.manager.verified)
        self.assertIn("87", self.manager.message)
        self.assertTrue(states)
        self.assertFalse(any(enabled for enabled, _message in states))

    def test_off_clears_registered_window_and_active_state(self):
        window = self.window()
        self.manager.register(window)
        self.assertTrue(self.manager.set_enabled(True))
        self.api.reset_mock()
        self.assertTrue(self.manager.set_enabled(False))
        self.api.assert_any_call(int(window.winId()), False)
        self.assertFalse(self.manager.verified)
        self.assertEqual(self.manager.message, "")

    def test_failed_dynamic_popup_recovers_after_hide_and_is_rechecked_on_show(self):
        main = self.window()
        main_handle = int(main.winId())
        self.api.side_effect = lambda hwnd, enabled: AffinityResult(
            True, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        ) if hwnd == main_handle else AffinityResult(False, message="popup error")
        self.manager.register(main)
        self.assertTrue(self.manager.set_enabled(True))
        popup = self.window()
        popup.setWindowFlags(Qt.WindowType.Popup)
        popup.show()
        self.app.processEvents()
        self.assertFalse(self.manager.verified)
        self.assertIn("popup error", self.manager.message)
        popup.hide()
        self.app.processEvents()
        self.assertTrue(self.manager.verified)
        self.assertEqual(self.manager.message, "")
        # The hidden popup is ignored only while hidden, not trusted forever.
        popup.show()
        self.app.processEvents()
        self.assertFalse(self.manager.verified)
        self.assertIn("popup error", self.manager.message)

    def test_destroyed_failed_dynamic_popup_does_not_leave_latched_error(self):
        main = self.window()
        main_handle = int(main.winId())
        self.api.side_effect = lambda hwnd, enabled: AffinityResult(
            True, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        ) if hwnd == main_handle else AffinityResult(False, message="popup error")
        self.manager.register(main)
        self.assertTrue(self.manager.set_enabled(True))
        popup = self.window()
        popup.setWindowFlags(Qt.WindowType.Popup)
        popup.show()
        self.app.processEvents()
        self.assertFalse(self.manager.verified)
        popup.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(isValid(popup))
        self.assertTrue(self.manager.verified)
        self.assertEqual(self.manager.message, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
