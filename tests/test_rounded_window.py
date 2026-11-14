"""Offscreen regressions for transparency, dark edges and real round cut-outs.

These checks do not capture the desktop, open audio devices or contact APIs.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from examai.ui.rounded_window import configure_rounded_window


class RoundedWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isValid(window):
                window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def window(self, **options):
        window = QWidget()
        window.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        window.resize(520, 700)
        self.windows.append(window)
        configure_rounded_window(window, **options)
        return window

    def test_default_transparency_uses_normal_native_surface(self):
        window = self.window()
        self.assertFalse(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertAlmostEqual(window.windowOpacity(), 0.95, places=2)
        self.assertAlmostEqual(window._rounded_window.opacity, 0.95)

    def test_invalid_nonfinite_opacity_falls_back_to_usable_default(self):
        for value in (None, "invalid", float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                window = self.window(opacity=value)
                actual = window._rounded_window.opacity
                self.assertTrue(math.isfinite(actual))
                self.assertEqual(actual, 0.95)
                self.assertAlmostEqual(window.windowOpacity(), 0.95, places=2)

    def test_opacity_respects_safe_limits_and_numeric_settings(self):
        for value, expected in ((-1, 0.30), (5, 1.0), ("0.72", 0.72), (0.6, 0.6)):
            with self.subTest(value=value):
                window = self.window(opacity=value)
                self.assertEqual(window._rounded_window.opacity, expected)
                self.assertAlmostEqual(window.windowOpacity(), expected, places=2)

    def test_all_four_corner_pixels_are_absent_but_center_is_present(self):
        window = self.window(radius=14)
        mask = window.mask()
        for point in (QPoint(0, 0), QPoint(519, 0), QPoint(0, 699), QPoint(519, 699)):
            with self.subTest(point=point):
                self.assertFalse(mask.contains(point))
        self.assertTrue(mask.contains(QPoint(260, 350)))
        self.assertTrue(mask.contains(QPoint(260, 0)))
        self.assertTrue(mask.contains(QPoint(0, 350)))
        self.assertEqual(mask.boundingRect().size(), QSize(520, 700))

    def test_resize_rebuilds_cutouts_for_current_window_dimensions(self):
        window = self.window(radius=20)
        window.show()
        self.app.processEvents()
        window.resize(760, 480)
        self.app.processEvents()
        mask = window.mask()
        self.assertEqual(mask.boundingRect().size(), QSize(760, 480))
        self.assertFalse(mask.contains(QPoint(759, 479)))
        self.assertTrue(mask.contains(QPoint(700, 240)))
        self.assertFalse(mask.contains(QPoint(260, 600)))

    def test_exposed_root_surface_is_dark_instead_of_white(self):
        window = self.window()
        self.assertTrue(window.autoFillBackground())
        self.assertEqual(window.palette().color(QPalette.ColorRole.Window), QColor("#0a0a0a"))
        custom = self.window(color="#0f0f0f")
        self.assertEqual(custom.palette().color(QPalette.ColorRole.Window), QColor("#0f0f0f"))

    def test_actual_main_window_uses_the_same_transparent_rounded_surface(self):
        # The original UI's two helper modules are available in the attachment
        # cache used by this workspace. A full installed application resolves
        # them normally from its own folder.
        reference = Path(__file__).resolve().parent.parent / "ghost_reference_ui"
        if reference.is_dir():
            sys.path.append(str(reference))
        try:
            from examai.config import DEFAULT_CONFIG
            from examai.ui.ui_main import MainWindow
        except ModuleNotFoundError as error:
            self.skipTest(f"Original UI helper modules are unavailable: {error.name}")

        class Settings:
            def set(self, key, value):
                pass

            def get(self, key, default=None):
                if key == "window_opacity":
                    return 0.82
                return DEFAULT_CONFIG.get(key, default)

        database = MagicMock()
        database.get_documents_list.return_value = []
        with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
            window = MainWindow(Settings(), database)
        self.windows.append(window)
        self.assertFalse(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertAlmostEqual(window.windowOpacity(), 1.0, places=2)
        self.assertFalse(window.mask().contains(QPoint(0, 0)))
        self.assertTrue(window.mask().contains(window.rect().center()))
        self.assertIn("#141518", window.styleSheet())


if __name__ == "__main__":
    unittest.main(verbosity=2)
