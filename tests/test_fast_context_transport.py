"""Exercise desktop, cloud and Google SDK at their HTTP boundaries."""
import json
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from google import genai
from google.genai import types
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QTest

from examai.ai.conversation import CONVERSATION_HEADER
from examai.ai.llm_engine import LLMEngine
from tests.test_context_stability import controller


@pytest.mark.parametrize("cloud", [False, True])
def test_fast_sdk_payload_and_first_chunk(tmp_path, monkeypatch, cloud):
    from server.api import create_app
    from server.store import Store, digest

    app, ctl = controller(tmp_path)
    posted = []
    released = Event()

    def sse(text, done=False):
        data = {"content": {"parts": [{"text": text}]}, **({"finishReason": "STOP"} if done else {})}
        return ("data: " + json.dumps({"candidates": [data]}) + "\n\n").encode()

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield sse("Перший фрагмент ")
            assert released.wait(6), "GUI had to display text before the provider finished"
            yield sse("і завершення.", True)

    def respond(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, stream=Chunks())

    def engine(key):
        item = LLMEngine(key)
        transport = item.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        item.gemini_client = item.clients.enter_context(genai.Client(
            api_key="synthetic", http_options=types.HttpOptions(httpx_client=transport)))
        return item

    if cloud:
        from examai.beta import BetaClient

        monkeypatch.setenv("EXAMAI_CLOUD_GEMINI_KEY", "fixture")
        monkeypatch.setattr("server.api.LLMEngine", engine)
        db = tmp_path / "server.db"
        api = create_app(db)
        store = Store(db)
        with store.transaction() as tx:
            token = store.issue(tx, "tester", "device")
            tx.execute("INSERT INTO invite(code) VALUES(?)", (digest("test"),))
            store.redeem(tx, "tester", "test")
        ctl.ui.account_panel.client = BetaClient("http://127.0.0.1", token)

        # ASGI TestClient buffers HTTP bodies. Run real TCP to prove streaming.
        import socket
        from threading import Thread
        import uvicorn

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(api, log_level="error", ws="none"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        ctl.ui.account_panel.client.url = f"http://127.0.0.1:{port}"
        for _ in range(400):
            if server.started:
                break
            QTest.qWait(10)
        assert server.started
    else:
        ctl.config.set("gemini_api_key", "fixture")
        ctl.llm = engine("fixture")
        ctl.llm_settings = "fixture"

    def send(question, *, voice=False):
        released.clear()
        if voice:
            ctl.audio = MagicMock()
            ctl.audio.isInterruptionRequested.return_value = False
            ctl.ui.auto_send.setChecked(True)
            ctl.handle_question(question, request_id=f"voice-{len(posted)}")
            ctl._finalize_voice_question(force=True)
        else:
            ctl.ui.input_manual_question.setText(question)
            ctl.ui.btn_send_q.click()
        worker = ctl.api
        assert worker is not None
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        probe = QTimer(interval=10)
        probe.timeout.connect(lambda: released.set() if "Перший фрагмент" in ctl.ui.txt_answer.toPlainText() else None)
        probe.start()
        QTimer.singleShot(8500, loop.quit)
        try:
            loop.exec()
            app.processEvents()
            assert released.is_set()
            assert ctl.api is None and "завершення" in ctl.ui.txt_answer.toPlainText()
        finally:
            released.set()
        return posted[-1]["contents"][0]["parts"][0]["text"]

    try:
        for question in ("Інтеграл", "Що таке Диференціювання?"):
            send(question, voice=cloud)
        payload = send("В чому різниця між ними?", voice=cloud)
        current_question, memory = payload.split(CONVERSATION_HEADER, 1)
        assert "Інтеграл" in current_question and "Диференціювання" in current_question
        assert "Інтеграл" in memory and "Диференціювання" in memory
        ctl.on_clear_all()
        for question in ("Що таке диз'юнкція?", "Що таке кон'юнкція?"):
            send(question)
        payload = send("Чим вони відрізняються?")
        assert CONVERSATION_HEADER in payload and "диз'юнкція" in payload and "кон'юнкція" in payload
        ctl.on_clear_all()
        names = ["пряма рекурсія", "взаємна рекурсія", "хвостова рекурсія"]
        for name in names:
            send(f"Що таке {name}?", voice=True)
        for followup in ("У чому між ними різниця?", "Чим їхня різниця?")[:1 if cloud else 2]:
            payload = send(followup, voice=followup.startswith("У"))
            # A generated comparison itself is a new turn; the initial comparison must contain all three definitions.
            if followup.startswith("У"):
                assert all(name in payload for name in names)
            else:
                assert CONVERSATION_HEADER in payload
        assert CONVERSATION_HEADER not in send("Як створити таблицю в SQLite?")
        ctl.on_clear_all()
        from pathlib import Path
        ctl.rag.index_file_sync(Path(__file__).parent / "fixtures" / "code.txt")
        payload = send("Що робить 58-й рядок у code.txt?")
        assert "58:" in payload and "double" in payload
        for followup in ("Детальніше розкажи", "А для чого потрібна ця змінна?"):
            payload = send(followup)
            assert all(anchor in payload for anchor in (CONVERSATION_HEADER, "code.txt", "58:", "double"))
    finally:
        ctl.audio = None
        ctl.shutdown()
        app.aboutToQuit.disconnect(ctl.shutdown)
        app.removeEventFilter(ctl.capture_privacy)
        ctl.ui.deleteLater()
        if cloud:
            server.should_exit = True
            thread.join(8)
