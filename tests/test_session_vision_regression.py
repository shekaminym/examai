import json
from unittest.mock import MagicMock

import httpx
import pytest
from google import genai
from google.genai import types

from examai.ai.conversation import Conversation
from examai.ai.llm_engine import GenerationError, LLMEngine, keep_timeout_local
from examai.database import DatabaseManager


def test_full_history_selects_an_older_topic_and_limits_provider_context():
    memory = Conversation()
    generation = memory.generation
    full = "Gradient points toward increasing values. " * 40
    memory.remember("Що таке градієнт?", full, generation)
    for i in range(55):
        memory.remember(f"Unrelated topic {i}", f"Unrelated answer {i}", generation)
    assert memory.turns[0][1] == full
    context = memory.context("Повернімося до градієнта")
    assert "Градієнт".casefold() in context.casefold()
    assert "Gradient points" in context and full not in context
    assert "Unrelated" not in context and len(context) < 1900
    memory.clear()
    memory.remember("Stale generation", "Ignored", generation)
    assert not memory.context("Explain further")


def test_saved_history_is_partitioned_without_losing_legacy_rows(tmp_path):
    db = DatabaseManager(tmp_path / "notes.db")
    db.save_qa("legacy", "answer", "test")
    db.save_qa("private", "full", "test", session="one", account="a", reference="code.txt:58")
    db.save_qa("other", "answer", "test", session="two", account="b")
    assert [item["question"] for item in db.get_history()] == ["legacy"]
    assert db.get_history(account="a", session="one") == [{"question": "private", "answer": "full"}]
    assert not db.get_history(account="a", session="two")


@pytest.mark.parametrize("failure,attempts,error", [
    ("connect", 2, None),
    ("timeout", 1, "аналіз зображення вчасно"),
    ("quota", 2, "квоти"),
    ("cancel", 0, "скасовано"),
])
def test_vision_deadline_retry_and_final_only(failure, attempts, error):
    requests = []
    def respond(request):
        requests.append(request)
        if failure == "connect" and len(requests) == 1:
            raise httpx.ConnectError("fixture")
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture")
        if failure == "quota":
            return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
        event = {"candidates": [{"content": {"parts": [{"text": "Final answer"}]}, "finishReason": "STOP"}]}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    engine = LLMEngine("synthetic")
    with engine.clients as stack:
        client = stack.enter_context(httpx.Client(transport=httpx.MockTransport(respond),
                                                   event_hooks={"request": [keep_timeout_local]}))
        engine.gemini_client = stack.enter_context(genai.Client(
            api_key="synthetic", http_options=types.HttpOptions(
                httpx_client=client, retry_options=types.HttpRetryOptions(attempts=1))))
        update = MagicMock()
        def generate():
            return engine.generate_image_answer(b"fixture", "image/png", on_text=update,
                                                cancelled=lambda: failure == "cancel")
        if error:
            with pytest.raises(GenerationError, match=error):
                generate()
        else:
            assert "Final answer" in generate()
            update.assert_called_once()
    assert len(requests) == attempts
    assert all(30 <= req.extensions["timeout"]["read"] <= 38
               and req.extensions["timeout"]["connect"] == 5 for req in requests)
