import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


CONFIG_FILE = "config.json"
KEY_ENV = {"gemini_api_key": "GEMINI_API_KEY"}

DEFAULT_CONFIG = {
    "gemini_model": "gemini-3.5-flash-lite",
    "whisper_model_size": "base",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "whisper_language": "uk",
    "whisper_previews": True,
    "whisper_neural_vad": False,
    "whisper_noise_reduction": False,
    "whisper_end_silence_ms": 550,
    "speech_auto_language": False,
    "local_previews": False,
    "vad_mode": "auto",
    "speech_end_silence_ms": 420,
    "speech_pace_mode": "auto",
    "speech_pipeline_version": 2,
    "visual_theme_version": 3,
    "ui_performance_mode": "auto",
    "resolved_profile": "",
    "theme": "midnight",
    "accent": "#8e93ff",
    "system_blur": True,
    "animation_intensity": "full",
    "reduce_motion": False,
    "window_mode": "docked",
    "dock_anchor": "center",
    "dock_screen": "",
    "floating_geometry": [],
    "auto_send": True,
    "answer_mode": "fast",
    "save_answer_history": False,
    "audio_device_index": -1,
    "audio_mode": "loopback",
    "audio_backend": "pyaudiowpatch",
    "always_on_top": True,
    "privacy_mode": False,
    "ghost_controls_locked": False,
    "font_size": 13,
    "window_opacity": 0.98,
    "gemini_api_key": "",
    "language": "UA"
}


class ConfigManager:
    def __init__(self, filepath=CONFIG_FILE):
        self.filepath = Path(filepath)
        self.config = self.load_config()

    def load_config(self):
        config = DEFAULT_CONFIG.copy()
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return config
        if not isinstance(data, dict):
            return config
        for name, default in DEFAULT_CONFIG.items():
            value = data.get(name)
            if type(value) is type(default):
                config[name] = value
        # Older versions saved "normal" even while splitting slow questions.
        # Upgrade once; subsequent explicit mode choices remain untouched.
        version = data.get("speech_pipeline_version", 0)
        if not isinstance(version, int) or version < 2:
            config["speech_pace_mode"] = "complete"
            config["speech_pipeline_version"] = 2
        visual_version = data.get("visual_theme_version", 0)
        if not isinstance(visual_version, int) or visual_version < 3:
            if config["window_opacity"] >= 0.94:
                config["window_opacity"] = 0.86
            config["visual_theme_version"] = 3
        if "theme" not in data:
            config["theme"] = "liquid_blue"
        for name in KEY_ENV:
            if isinstance(data.get(name), str) and data[name].strip():
                config[name] = data[name].strip()
        if data.get("audio_backend") != DEFAULT_CONFIG["audio_backend"]:
            config["audio_device_index"] = -1
        return config

    def save_config(self, new_config=None):
        updated = self.config | (new_config or {})
        # Preserve keys already on disk while keeping newly entered keys session-only.
        try:
            original = json.loads(self.filepath.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            original = {}
        persisted = dict(original) if isinstance(original, dict) else {}
        persisted.update({name: value for name, value in updated.items() if name not in KEY_ENV})
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.filepath.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump(persisted, file, ensure_ascii=False, indent=4)
            temporary.replace(self.filepath)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.config = updated

    def get(self, key, default=None):
        if key in KEY_ENV:
            return self.config.get(key, "") or os.environ.get(KEY_ENV[key], "").strip()
        return self.config.get(key, default)

    def set(self, key, value):
        self.save_config({key: value})
