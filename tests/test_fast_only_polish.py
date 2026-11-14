"""FAST-only behavior and the isolated subscription mockup."""

import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QVariantAnimation
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from examai.ai.llm_engine import LLMEngine
from examai.app import SplashScreen
from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.motion import MotionManager
from examai.ui.subscriptions import FEATURES, PLANS, SubscriptionsPage
from examai.ui.ui_main import MainWindow, SettingsNavigationDelegate


def make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "config.json")
    config.save_config({"window_mode": "floating", "reduce_motion": True})
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]), \
         patch("examai.ui.ui_main.set_windows_backdrop", return_value=False):
        window = MainWindow(config, DatabaseManager(tmp_path / "db.sqlite"))
    window.show()
    window.resize(940, 620)
    app.processEvents()
    return app, config, window


def dispose(app, window):
    window.performance_monitor.timer.stop()
    window.deleteLater()
    app.processEvents()


def test_old_smart_setting_is_always_fast(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"answer_mode": "smart"}), encoding="utf-8")
    config = ConfigManager(path)
    assert config.get("answer_mode") == "fast"
    config.save_config({"answer_mode": "smart"})
    assert json.loads(path.read_text(encoding="utf-8"))["answer_mode"] == "fast"


def test_retired_mode_argument_keeps_original_fast_prompt_and_budget():
    engine = LLMEngine("synthetic")
    with patch.object(engine, "_generate", return_value="ok") as generate:
        engine.generate_answer("Що таке граф?", "", mode="smart")
    assert generate.call_args.args[0].startswith("Почни з відповіді українською")
    assert generate.call_args.args[2] == 140
    with patch.object(engine, "_generate", return_value="ok") as generate:
        engine.generate_image_answer(b"image", "image/png", mode="smart")
    assert generate.call_args.args[2] == 260


def test_subscription_layout_matches_reference_and_has_no_billing(tmp_path):
    app, config, window = make_window(tmp_path)
    try:
        assert not hasattr(window, "answer_mode")
        page = window.tab_subscriptions
        assert isinstance(page, SubscriptionsPage)
        window.tabs.setCurrentWidget(page)
        app.processEvents()
        assert page._columns == 4
        assert page.verticalScrollBar().maximum() == 0
        assert [(card.term_label.text(), card.price_label.text()) for card in page.cards] == list(PLANS)
        assert page.cards[1].badge.text() == "Найвигідніше"
        assert all([label.text() for label in card.features] == list(FEATURES)
                   for card in page.cards)
        assert all(not card.order_button.isEnabled() for card in page.cards)
        assert not any("API-ключ користувача" in label.text()
                       for label in page.findChildren(QLabel))
        window.resize(640, 620)
        app.processEvents()
        assert page._columns == 2
    finally:
        dispose(app, window)


def test_tint_drag_previews_without_disk_write_or_full_restyle(tmp_path):
    app, config, window = make_window(tmp_path)
    try:
        window.open_settings()
        slider = window.tint_slider
        before = config.get("window_opacity")
        with patch.object(config, "save_config", wraps=config.save_config) as save, \
             patch.object(window, "apply_aurora_theme", wraps=window.apply_aurora_theme) as restyle:
            slider.setSliderDown(True)
            slider.setValue(75)
            app.processEvents()
            assert config.get("window_opacity") == before
            assert save.call_count == 0
            assert restyle.call_count == 0
            assert window.tint_percent.text() == "75%"
            slider.setSliderDown(False)
            app.processEvents()
            assert config.get("window_opacity") == .75
            assert save.call_count == 1
            assert restyle.call_count == 1
    finally:
        dispose(app, window)


def test_settings_keyboard_focus_and_both_languages(tmp_path):
    app, config, window = make_window(tmp_path)
    try:
        window.open_settings()
        nav = window.settings_navigation
        assert isinstance(nav.itemDelegate(), SettingsNavigationDelegate)
        for language in ("UA", "ENG"):
            window.change_language(language)
            for row in range(nav.count()):
                nav.setCurrentRow(row)
                nav.setFocus()
                app.processEvents()
                assert nav.currentRow() == row
                assert nav.item(row).text()
            nav.setCurrentRow(0)
            nav.setFocus()
            QTest.keyClick(nav, Qt.Key.Key_Down)
            assert nav.currentRow() == 1
    finally:
        dispose(app, window)


def test_off_motion_stops_animations_and_qt_combo_effect(tmp_path):
    app, config, window = make_window(tmp_path)
    try:
        animation = QVariantAnimation()
        animation.setStartValue(0)
        animation.setEndValue(1)
        animation.setDuration(1000)
        window.motion.register(animation)
        window.save_appearance("reduce_motion", False)
        window.save_appearance("animation_intensity", "full")
        animation.start()
        assert animation.state() == QVariantAnimation.State.Running
        window.save_appearance("animation_intensity", "off")
        assert window.motion.level == "off"
        assert window.motion_ms(200) == 0
        assert animation.state() == QVariantAnimation.State.Stopped
        assert not app.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        assert not window.live_cards.loading_timer.isActive()
    finally:
        dispose(app, window)


def test_minimal_splash_shows_ready_window_without_fixed_wait():
    app = QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QWidget
    main = QWidget()
    splash = SplashScreen(main, reduced_motion=True)
    done = []
    splash.finished.connect(lambda: done.append(True))
    splash.start_splash()
    app.processEvents()
    assert done == [True]
    assert main.isVisible()
    assert not splash.spinner.timer.isActive()
    assert [label.text() for label in splash.findChildren(QLabel)] == ["EXAM AI"]
    main.close()
    splash.close()


def test_system_reduce_motion_forces_off_without_touching_speech():
    app = QApplication.instance() or QApplication([])
    manager = MotionManager(app, system_reduced=True)
    try:
        manager.configure(profile="premium", intensity="full", reduce_motion=False)
        assert manager.level == "off"
        assert manager.duration(200) == 0
    finally:
        manager.restore()
