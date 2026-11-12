"""Controlled Gemini SSE replay through the SDK, real Qt controller and response widget."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter, sleep
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--real", action="store_true", help="Use the original eight-second deadline")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source).resolve()))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import httpx
    from google import genai
    from google.genai import types
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    import main as app_module
    from config import ConfigManager
    from database import DatabaseManager
    from llm_engine import LLMEngine

    def event(text, finish=False):
        candidate = {"content": {"parts": [{"text": text}]}}
        if finish:
            candidate["finishReason"] = "STOP"
        return ("data: " + json.dumps({"candidates": [candidate]}) + "\n\n").encode()

    class Fixture(httpx.SyncByteStream):
        def __iter__(self):
            sleep(.015)
            yield event("First")
            sleep(8.2 if args.real else .11)
            yield event(" answer", finish=True)

    def response(_request):
        return httpx.Response(200, stream=Fixture())

    app = QApplication([])
    if not args.real and hasattr(app_module, "ANSWER_TIMEOUT_SECONDS"):
        app_module.ANSWER_TIMEOUT_SECONDS = .08
    first_ms, complete_ms = [], []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with (patch.object(app_module, "ConfigManager", return_value=ConfigManager(root / "config.json")),
              patch.object(app_module, "DatabaseManager", return_value=DatabaseManager(root / "local.db")),
              patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
            controller = app_module.MainController(app)

        engine = LLMEngine("synthetic")
        client = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(response)))
        engine.gemini_client = engine.clients.enter_context(
            genai.Client(api_key="synthetic", http_options=types.HttpOptions(httpx_client=client))
        )
        controller.llm = engine
        controller.get_llm = lambda *_args: engine
        # In the baseline, expiry detaches the engine. Give each attempt an isolated session.
        for _ in range(args.repeats):
            if engine.gemini_client is None:
                raise RuntimeError("SDK client missing")
            controller.llm = None  # The fixture owns this client across trials.
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(2)
            measured_first = False
            started = perf_counter()

            def observe():
                nonlocal measured_first
                elapsed = (perf_counter() - started) * 1000
                text = controller.ui.txt_answer.toPlainText()
                if not measured_first and "First" in text:
                    first_ms.append(elapsed)
                    measured_first = True
                if controller.api is None and not controller._retired_answers:
                    if "First answer" in text:
                        complete_ms.append(elapsed)
                    loop.quit()

            timer.timeout.connect(observe)
            controller.submit_manual_question("synthetic")
            timer.start()
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
            timer.stop()
        engine.close()
        app.aboutToQuit.disconnect(controller.shutdown)

    def summary(samples):
        if not samples:
            return "unavailable"
        ordered = sorted(samples)
        return f"p50={statistics.median(ordered):.1f} p95={ordered[math.ceil(.95 * len(ordered)) - 1]:.1f}"

    print(f"first_ui_ms {summary(first_ms)}")
    print(f"complete_ui_ms {summary(complete_ms)}")
    print(f"completed={len(complete_ms)}/{args.repeats}")


if __name__ == "__main__":
    main()
