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
    "window_opacity": 0.95,
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
        for name in KEY_ENV:
            if isinstance(data.get(name), str) and data[name].strip():
                config[name] = data[name].strip()
        if data.get("audio_backend") != DEFAULT_CONFIG["audio_backend"]:
            config["audio_device_index"] = -1
        return config

    def save_config(self, new_config=None):
        updated = self.config | (new_config or {})
        # Never write API credentials to disk: preserve them for this session only.
        persisted = {name: value for name, value in updated.items() if name not in KEY_ENV}
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
