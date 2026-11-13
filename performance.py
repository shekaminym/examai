"""Hardware recommendation and conservative runtime adaptation for EXAM AI."""
from dataclasses import dataclass
from functools import lru_cache
import logging
import sys

import psutil
from PySide6.QtCore import QObject, QTimer, Qt
from time import perf_counter

GIB = 1024 ** 3
PROFILES = ("eco", "balanced", "premium")
timing = logging.getLogger("phantom.timing")
LEGACY = {"performance": "eco", "visual": "premium"}


@dataclass(frozen=True)
class Hardware:
    logical: int
    physical: int
    ram_gib: float
    available_gib: float
    cuda_devices: int = 0
    windows_build: int = 0
    scale: float = 1.0


@lru_cache(maxsize=1)
def cuda_device_count():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count()
    except (ImportError, OSError, RuntimeError):
        return 0


def detect_hardware(screen, cuda_devices=None):
    if cuda_devices is None:
        cuda_devices = cuda_device_count()
    memory = psutil.virtual_memory()
    logical = psutil.cpu_count() or 1
    return Hardware(logical, psutil.cpu_count(logical=False) or logical,
                    memory.total / GIB, memory.available / GIB, cuda_devices,
                    sys.getwindowsversion().build if sys.platform == "win32" else 0,
                    screen.devicePixelRatio() if screen else 1.0)


def recommend(hardware):
    if hardware.ram_gib < 7 or hardware.available_gib < 2 or hardware.physical < 4:
        return "eco"
    if hardware.ram_gib >= 15 and hardware.available_gib >= 6 and (
        hardware.physical >= 8 or (hardware.physical >= 6 and hardware.cuda_devices)
    ):
        return "premium"
    return "balanced"


class RuntimePolicy:
    def __init__(self, ceiling):
        self.profile = ceiling
        self.bad = 0

    def observe(self, *, lag_ms, available_gib, process_cpu, fps=None):
        pressured = (lag_ms > 55 or available_gib < 1.25 or process_cpu > 175
                     or (fps is not None and fps < 27))
        self.bad = self.bad + 1 if pressured else 0
        if self.bad >= 3 and self.profile != "eco":
            self.profile = PROFILES[PROFILES.index(self.profile) - 1]
            self.bad = 0
            return self.profile
        return None


class PerformanceMonitor(QObject):
    def __init__(self, parent, policy, update):
        super().__init__(parent)
        self.policy = policy
        self.update = update
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.samples = {}
        self.last_tick = perf_counter()
        self.worst_lag = 0
        self.ticks = 0
        self.fps = None
        self.timer = QTimer(self, interval=200, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.sample)
        self.timer.start()

    def sample(self):
        now = perf_counter()
        self.worst_lag = max(self.worst_lag, (now - self.last_tick) * 1000 - 200)
        self.last_tick = now
        self.ticks += 1
        if self.ticks < 10:
            return
        self.ticks = 0
        self.samples = {
            "lag_ms": self.worst_lag,
            "process_cpu": self.process.cpu_percent(),
            "available_gib": psutil.virtual_memory().available / GIB,
            "rss_mib": self.process.memory_info().rss / 1024**2,
            "fps": self.fps,
        }
        profile = self.policy.observe(lag_ms=self.worst_lag,
                                      available_gib=self.samples["available_gib"],
                                      process_cpu=self.samples["process_cpu"], fps=self.fps)
        timing.info("gui_lag_ms=%.1f cpu_pct=%.1f rss_mib=%.1f",
                    self.worst_lag, self.samples["process_cpu"], self.samples["rss_mib"])
        self.worst_lag = 0
        self.fps = None
        self.update(profile, self.samples)
