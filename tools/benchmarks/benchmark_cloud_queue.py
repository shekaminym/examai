"""Exercise real HTTP streaming with isolated accounts and an optional real Gemini provider."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic, sleep

import httpx
import uvicorn

from examai.ai.llm_engine import ProviderRateLimit
from server import api, cloud_queue
from server.store import Store


class FixtureProvider:
    lock = Lock()
    limited = False

    def __init__(self, _key):
        pass

    def close(self):
        pass

    def generate_answer(self, question, context, on_text, **_options):
        with self.lock:
            limit = not self.__class__.limited
            self.__class__.limited = True
        if limit:
            raise ProviderRateLimit(5)
        sleep(0.32)
        self.usage_callback(140, 20)
        on_text("Ready")
        return "Ready"


async def submit(origin, tokens):
    async with httpx.AsyncClient(base_url=origin, timeout=55, limits=httpx.Limits(max_connections=50)) as client:
        gate = asyncio.Event()

        async def ask(index, token):
            await gate.wait()
            start = monotonic()
            result = {"account": index, "waited": False, "starts": 0, "last": None}
            try:
                async with client.stream("POST", "/answer", headers={"Authorization": "Bearer " + token},
                                         json={"queue": True, "language": "en",
                                               "question": "What is two plus two? Give one word."}) as response:
                    result["http"] = response.status_code
                    if not response.is_success:
                        result["last"] = json.loads(await response.aread()).get("detail")
                        return result
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        if "queued" in event:
                            result["waited"] = True
                            result["position"] = max(event["queued"], result.get("position", 0))
                        if "started" in event:
                            result["starts"] += 1
                            result.setdefault("first_start_seconds", round(monotonic() - start, 2))
                        if "final" in event or "error" in event:
                            result["last"] = "final" if "final" in event else event["error"]
                            result["total_seconds"] = round(monotonic() - start, 2)
                            break
            except httpx.HTTPError as exc:
                result["last"] = type(exc).__name__
            return result

        tasks = [asyncio.create_task(ask(i, token)) for i, token in enumerate(tokens)]
        gate.set()
        return await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, choices=range(20, 31), default=25)
    parser.add_argument("--live-provider", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.live_provider:
        if not os.getenv("EXAMAI_CLOUD_GEMINI_KEY"):
            parser.error("Use the configured Gemini key in the server environment.")
        # Leave headroom for the existing production site on the same project.
        cloud_queue.PROVIDER_RPM = 10
    else:
        os.environ["EXAMAI_CLOUD_GEMINI_KEY"] = "synthetic"
        api.LLMEngine = FixtureProvider
    os.environ["EXAMAI_CLOUD_MAX_PER_MINUTE"] = "24"
    with TemporaryDirectory() as directory, socket.socket() as listener:
        path = Path(directory) / "bench.sqlite"
        store = Store(path)
        tokens = []
        with store.transaction() as tx:
            for index in range(args.users):
                sub = f"load-test-{index}"
                tokens.append(store.issue(tx, sub, "fixture-device"))
                tx.execute("UPDATE account SET state='BETA',remaining=1800 WHERE sub=?", (sub,))
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(api.create_app(path), log_level="error", access_log=False))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        started = monotonic()
        try:
            while not server.started:
                if not thread.is_alive() or monotonic() - started > 10:
                    raise RuntimeError("Benchmark server did not start")
                sleep(0.05)
            replies = asyncio.run(submit(f"http://127.0.0.1:{listener.getsockname()[1]}", tokens + tokens[:3]))
        finally:
            server.should_exit = True
            thread.join(10)
        finished = [item for item in replies if item["last"] == "final"]
        waits = [item.get("first_start_seconds", 0) for item in finished]
        with store.transaction() as tx:
            models = dict(tx.execute("SELECT model,count(*) FROM cloud_provider_usage GROUP BY model"))
            debits = [1800 - row[0] for row in tx.execute("SELECT remaining FROM account")]
            distinct_debits = sum(debit > 0 for debit in debits)
            usage = tx.execute("SELECT sum(requests) FROM usage").fetchone()[0]
        payload = {
            "provider": "live Gemini Free Tier" if args.live_provider else "simulated latency and one 429",
            "accounts": len(tokens),
            "requests_including_duplicate_submissions": len(replies),
            "queued_requests": sum(item["waited"] for item in replies),
            "completed": len(finished),
            "non_completed_by_reason": dict(Counter(item["last"] for item in replies if item["last"] != "final")),
            "largest_queue_position": max(item.get("position", 0) for item in replies),
            "max_first_start_seconds": max(waits, default=0),
            "max_total_seconds": max((item.get("total_seconds", 0) for item in finished), default=0),
            "provider_attempts_by_model": models,
            "accounts_charged": distinct_debits,
            "logical_admissions": usage,
            "max_active_seconds_debited": round(max(debits), 2),
            "requests": replies,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(encoded, encoding="utf8")
        print(json.dumps({key: value for key, value in payload.items() if key != "requests"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
