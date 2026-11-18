import base64
import json
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
import pytest

from examai.updates import verify_manifest
from server.api import create_app
from server.payments import signature
from server.store import Denied, Store, digest


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_server_invite_admin_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_ADMIN_SUBJECTS", "admin-sub")
    path = tmp_path / "server.sqlite"
    client = TestClient(create_app(path))
    store = Store(path)
    with store.transaction() as db:
        admin = bearer(store.issue(db, "admin-sub", "admin-device"))
        user = bearer(store.issue(db, "beta-sub", "user-device"))
    body = {"sub": "beta-sub", "action": "invite"}
    assert client.post("/admin/action", headers=user, json=body).status_code == 403
    code = client.post("/admin/action", headers=admin, json=body).json()["code"]
    assert client.post("/invite", headers=user, json={"code": code}).json()["seconds"] == 1800
    assert client.post("/invite", headers=user, json={"code": code}).status_code == 409
    issue = client.post("/reports", headers=user, json={"kind": "Bug", "description": "Cannot open Notes"}).json()
    result = client.post("/admin/action", headers=admin, json={
        "sub": "beta-sub", "action": "report", "report_id": issue["id"], "status": "Fixed"})
    assert result.is_success
    assert client.get("/reports", headers=user).json()[0]["status"] == "Fixed"
    grant = {"sub": "beta-sub", "action": "minutes", "amount": 10, "operation_id": str(uuid4())}
    assert client.post("/admin/action", headers=admin, json=grant).is_success
    assert client.post("/admin/action", headers=admin, json=grant).is_success
    assert client.post("/admin/action", headers=admin, json={**grant, "amount": 30}).status_code == 409
    assert client.get("/account", headers=user).json()["seconds"] == 2400
    for action, state in (("suspend", "SUSPENDED"), ("restore", "BETA")):
        assert client.post("/admin/action", headers=admin,
                           json={"sub": "beta-sub", "action": action}).is_success
        assert client.get("/account", headers=user).json()["state"] == state
    assert client.post("/logout", headers=user).is_success
    assert client.get("/account", headers=user).status_code == 401


def test_active_time_server_clock_exclusive_and_bounded(tmp_path):
    now = [1000]
    store = Store(tmp_path / "server.sqlite", now=lambda: now[0])
    with store.transaction() as db:
        a = digest(store.issue(db, "beta", "device-1"))
        b = digest(store.issue(db, "beta", "device-2"))
        db.execute("INSERT INTO invite(code) VALUES(?)", (digest("valid"),))
        store.redeem(db, "beta", "valid")
        now[0] += 90
        assert store.access(db, "beta")["seconds"] == 1800
        assert store.access(db, "beta", token=a, lease_action="start")["granted"]
        now[0] += 4
        assert not store.access(db, "beta", token=b, lease_action="start")["granted"]
        assert store.access(db, "beta", token=a, lease_action="stop")["seconds"] == 1796
        assert store.access(db, "beta", token=b, lease_action="start")["granted"]
        now[0] += 300
        assert store.access(db, "beta")["seconds"] == 1781


def test_expired_invite_cannot_be_redeemed(tmp_path):
    store = Store(tmp_path / "server.sqlite", now=lambda: 100)
    with store.transaction() as db:
        store.issue(db, "beta", "desktop")
        code = digest("expired-code")
        db.execute("INSERT INTO invite(code) VALUES(?)", (code,))
        db.execute("INSERT INTO invite_expiry VALUES(?,?)", (code, 100))
        with pytest.raises(Denied):
            store.redeem(db, "beta", "expired-code")
        assert store.access(db, "beta")["state"] == "EXPIRED"


def test_report_attachment_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_ADMIN_SUBJECTS", "admin-sub")
    path = tmp_path / "server.sqlite"
    client = TestClient(create_app(path))
    store = Store(path)
    with store.transaction() as db:
        admin = bearer(store.issue(db, "admin-sub", "admin-device"))
        user = bearer(store.issue(db, "user-sub", "user-device"))
    report = {"kind": "Bug", "description": "Cannot open notes", "diagnostics": "OS: Windows"}
    assert client.post("/reports", headers=user, json={**report, "screenshot": "broken"}).status_code == 422
    report_id = client.post("/reports", headers=user, json=report).json()["id"]
    route = f"/admin/reports/{report_id}/diagnostics"
    assert client.get(route, headers=user).status_code == 403
    response = client.get(route, headers=admin)
    assert response.content == b"OS: Windows"
    assert response.headers["content-disposition"] == 'attachment; filename="diagnostics.txt"'


def test_sandbox_requires_signature_and_records_callback_once(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_PUBLIC_URL", "https://beta.example.test")
    monkeypatch.setenv("EXAMAI_LIQPAY_SANDBOX_PUBLIC", "sandbox_test_merchant")
    monkeypatch.setenv("EXAMAI_LIQPAY_SANDBOX_PRIVATE", "fake-test-only")
    path = tmp_path / "server.sqlite"
    client = TestClient(create_app(path))
    store = Store(path)
    with store.transaction() as db:
        headers = bearer(store.issue(db, "buyer", "device"))
    url = client.post("/payments/checkout/1", headers=headers).json()["url"]
    assert client.get(url).is_success
    entry = client.get("/payments", headers=headers).json()[0]
    payload = dict(order_id=entry["id"], amount=149, currency="UAH", action="pay",
                   status="sandbox", public_key="sandbox_test_merchant")
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    assert client.post("/payments/callback", data={"data": data, "signature": "bad"}).status_code == 400
    for _ in range(2):
        assert client.post("/payments/callback", data={"data": data,
                           "signature": signature("fake-test-only", data)}).is_success
    assert client.get("/payments", headers=headers).json()[0]["status"] == "sandbox"
    assert client.get("/account", headers=headers).json()["state"] == "EXPIRED"
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM audit").fetchone()[0] == 1


def test_update_manifest_rejects_tampering_and_wrong_channel(monkeypatch):
    monkeypatch.setattr("examai.updates.VERSION", "3.1.0b1")
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public = base64.b64encode(raw).decode()
    manifest = json.dumps(dict(channel="beta", version="3.1.0", size=1024,
                               url="https://beta.example.test/setup.exe")).encode()
    envelope = {"manifest": base64.b64encode(manifest).decode(),
                "signature": base64.b64encode(key.sign(manifest)).decode()}
    assert verify_manifest(envelope, public, "beta")["version"] == "3.1.0"
    with pytest.raises(ValueError):
        verify_manifest(envelope, public, "stable")
    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        verify_manifest(dict(envelope, manifest="e30="), public, "beta")


def test_pkce_nonce_and_one_time_claim(tmp_path, monkeypatch):
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("EXAMAI_PUBLIC_URL", "https://beta.example.test")
    monkeypatch.setenv("EXAMAI_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("EXAMAI_GOOGLE_CLIENT_SECRET", "test-secret")
    client = TestClient(create_app(tmp_path / "oauth.sqlite"))
    flow = client.post("/oauth/start", json={"device": "desktop-fixture-0001"}).json()
    values = parse_qs(urlsplit(flow["url"]).query)
    assert values["code_challenge_method"] == ["S256"]
    assert client.post("/oauth/claim", json={"claim": flow["claim"]}).json()["pending"]

    def exchange(_url, **kwargs):
        verifier = kwargs["data"]["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert values["code_challenge"] == [challenge]
        import httpx
        return httpx.Response(200, json={"id_token": "fixture"}, request=httpx.Request("POST", _url))

    monkeypatch.setattr("server.api.httpx.post", exchange)
    monkeypatch.setattr("server.api.verify_oauth2_token", lambda *_: {"sub": "google-sub", "nonce": values["nonce"][0]})
    query = {"state": values["state"][0], "code": "test-code"}
    assert client.get("/oauth/callback", params=query).status_code == 200
    assert client.get("/oauth/callback", params=query).status_code == 400
    session = client.post("/oauth/claim", json={"claim": flow["claim"]}).json()["token"]
    assert client.get("/account", headers=bearer(session)).json()["state"] == "EXPIRED"
    assert client.post("/oauth/claim", json={"claim": flow["claim"]}).status_code == 410


def test_cloud_requires_lease_and_streams_once(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_CLOUD_GEMINI_KEY", "test-only")
    path = tmp_path / "cloud.sqlite"
    client = TestClient(create_app(path))
    store = Store(path)
    with store.transaction() as db:
        headers = bearer(store.issue(db, "beta", "device"))
        db.execute("INSERT INTO invite(code) VALUES(?)", (digest("example"),))
        store.redeem(db, "beta", "example")
    calls = []

    class Engine:
        def __init__(self, key):
            assert key == "test-only"

        def close(self):
            pass

        def generate_answer(self, question, context, on_text, *, language="uk"):
            calls.append((question, context))
            on_text("First fragment")
            return "First fragment and rest"

    monkeypatch.setattr("server.api.LLMEngine", Engine)
    question = {"question": "What is RAG?", "context": "fixture"}
    assert client.post("/answer", json=question, headers=headers).status_code == 403
    assert not calls
    assert client.post("/activity/start", headers=headers).json()["granted"]
    response = client.post("/answer", json=question, headers=headers)
    assert [json.loads(line) for line in response.text.splitlines()] == [
        {"text": "First fragment"}, {"final": "First fragment and rest"},
    ]
    assert calls == [("What is RAG?", "fixture")]


def test_cloud_budget_meters_tokens_and_stops_before_next_provider_request(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_CLOUD_GEMINI_KEY", "test-only")
    monkeypatch.setenv("EXAMAI_CLOUD_DAILY_BUDGET_MICROUSD", "20")
    monkeypatch.setenv("EXAMAI_CLOUD_RESERVE_MICROUSD", "12")
    monkeypatch.setenv("EXAMAI_CLOUD_INPUT_USD_PER_MILLION", "0.5")
    monkeypatch.setenv("EXAMAI_CLOUD_OUTPUT_USD_PER_MILLION", "2")
    path = tmp_path / "budget.sqlite"
    client = TestClient(create_app(path))
    store = Store(path)
    with store.transaction() as db:
        headers = bearer(store.issue(db, "beta", "device"))
        db.execute("INSERT INTO invite(code) VALUES(?)", (digest("example"),))
        store.redeem(db, "beta", "example")

    class Engine:
        calls = 0

        def __init__(self, key):
            pass

        def close(self):
            pass

        def generate_answer(self, question, context, on_text, *, language="uk"):
            Engine.calls += 1
            self.usage_callback(4, 4)
            return "Done"

    monkeypatch.setattr("server.api.LLMEngine", Engine)
    assert client.post("/activity/start", headers=headers).json()["granted"]
    assert client.post("/answer", json={"question": "first"}, headers=headers).is_success
    assert client.post("/answer", json={"question": "second"}, headers=headers).status_code == 429
    assert Engine.calls == 1
    with store.transaction() as db:
        assert tuple(db.execute("SELECT input_tokens,output_tokens,micro_usd FROM cloud_charge").fetchone()) == (4, 4, 10)
        assert db.execute("SELECT requests FROM usage").fetchone()[0] == 1


def test_cloud_rolling_limit_survives_restart_and_rejections_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMAI_CLOUD_GEMINI_KEY", "test-only")
    monkeypatch.setenv("EXAMAI_CLOUD_MAX_PER_MINUTE", "2")
    path = tmp_path / "rate.sqlite"
    store = Store(path)
    accounts = []
    with store.transaction() as tx:
        for sub in ("one", "two", "three"):
            accounts.append(bearer(store.issue(tx, sub, "device")))
            tx.execute("UPDATE account SET state='ACTIVE' WHERE sub=?", (sub,))

    class Engine:
        def __init__(self, key):
            pass

        def close(self):
            pass

        def generate_answer(self, question, context, on_text, *, language):
            return "Done"

    monkeypatch.setattr("server.api.LLMEngine", Engine)
    client = TestClient(create_app(path))
    body = {"question": "test"}
    for headers in accounts[:2]:
        assert client.post("/activity/start", headers=headers).json()["granted"]
        assert client.post("/answer", headers=headers, json=body).is_success
    restarted = TestClient(create_app(path))
    assert restarted.post("/activity/start", headers=accounts[2]).json()["granted"]
    denied = restarted.post("/answer", headers=accounts[2], json=body)
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) > 0
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM usage WHERE sub='three'").fetchone()[0] == 0
        tx.execute("UPDATE cloud_admission SET admitted=admitted-61")
    assert restarted.post("/answer", headers=accounts[2], json=body).is_success


@pytest.mark.parametrize("failure", [429, 503])
def test_queued_answer_retries_another_model_without_a_second_charge(tmp_path, monkeypatch, failure):
    from examai.ai.llm_engine import ProviderRateLimit, ProviderUnavailable
    from server.cloud_queue import MODELS

    monkeypatch.setenv("EXAMAI_CLOUD_GEMINI_KEY", "queue-fixture")
    monkeypatch.setenv("EXAMAI_CLOUD_DAILY_BUDGET_MICROUSD", "1000000")
    monkeypatch.setenv("EXAMAI_CLOUD_RESERVE_MICROUSD", "350000")
    monkeypatch.setenv("EXAMAI_CLOUD_INPUT_USD_PER_MILLION", "0.30")
    monkeypatch.setenv("EXAMAI_CLOUD_OUTPUT_USD_PER_MILLION", "2.50")
    path = tmp_path / "queue.sqlite"
    store = Store(path)
    with store.transaction() as tx:
        headers = bearer(store.issue(tx, "beta-user", "device"))
        tx.execute("UPDATE account SET state='BETA',remaining=1800 WHERE sub='beta-user'")
    calls = []

    class Engine:
        def __init__(self, _key):
            pass

        def close(self):
            pass

        def generate_answer(self, question, context, on_text, **kwargs):
            calls.append(self.gemini_model)
            if len(calls) == 1:
                if failure == 429:
                    raise ProviderRateLimit(60)
                raise ProviderUnavailable("provider busy")
            self.usage_callback(300, 12)
            on_text("Done")
            return "Done"

    monkeypatch.setattr("server.api.LLMEngine", Engine)
    client = TestClient(create_app(path))
    assert client.get("/cloud/capabilities").json()["answer_queue"] is True
    reply = client.post("/answer", headers=headers, json={"question": "test", "queue": True})
    events = [json.loads(line) for line in reply.text.splitlines()]
    assert events[-1] == {"final": "Done"}
    assert sum("started" in event for event in events) == 2
    assert calls == list(MODELS)
    with store.transaction() as tx:
        assert tx.execute("SELECT requests FROM usage").fetchone()[0] == 1
        assert tx.execute("SELECT count(*),sum(micro_usd) FROM cloud_charge").fetchone()[:] == (1, 120)
        assert tx.execute("SELECT model FROM cloud_provider_pause").fetchone()[0] == MODELS[0]
        assert tx.execute("SELECT count(*) FROM lease").fetchone()[0] == 0
        remaining = tx.execute("SELECT remaining FROM account").fetchone()[0]
        assert 1799 < remaining < 1800


def test_model_budgets_rpm_tpm_rpd_and_pacific_reset(tmp_path, monkeypatch):
    from datetime import datetime
    from server import cloud_queue as caps

    store = Store(tmp_path / "quotas.sqlite")
    now = datetime(2026, 11, 18, 23, 59, 58, tzinfo=caps.PACIFIC).timestamp()
    monkeypatch.setattr(caps, "PROVIDER_RPM", 2)
    monkeypatch.setattr(caps, "PROVIDER_TPM", 100)
    monkeypatch.setattr(caps, "PROVIDER_RPD", 3)
    first, second = caps.MODELS
    with store.transaction() as tx:
        caps.reserve(tx, "a", first, now - 2, 80)
        assert caps.candidate(tx, now, 30)[0] == second  # first would exceed TPM
        caps.reserve(tx, "b", second, now - 2, 10)
        caps.reserve(tx, "c", second, now - 1, 10)
        assert caps.candidate(tx, now, 30)[0] is None  # second would exceed RPM
        caps.reserve(tx, "d", first, now - 180, 1)
        caps.reserve(tx, "e", first, now - 180, 1)
        assert caps.candidate(tx, now, 1, (second,))[0] is None  # first exceeded RPD
        assert caps.candidate(tx, now + 3, 1, (second,))[0] == first


def test_windows_cloud_transport_shows_queue_events_without_starting_legacy_lease(monkeypatch):
    import httpx
    from examai.beta import BetaClient, CloudEngine

    calls = []
    events = [{"queued": 3}, {"queued": 1}, {"started": True}, {"text": "Four"}, {"final": "Four"}]

    def response(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/cloud/capabilities":
            return httpx.Response(200, json={"answer_queue": True})
        assert request.url.path == "/answer"
        assert json.loads(request.content)["queue"] is True
        return httpx.Response(200, text="\n".join(map(json.dumps, events)))

    with httpx.Client(transport=httpx.MockTransport(response)) as transport:
        monkeypatch.setattr("examai.beta.httpx.request", transport.request)
        monkeypatch.setattr("examai.beta.httpx.stream", transport.stream)
        client = BetaClient("https://beta.example.test", "token")
        statuses = []
        text = []
        answer = CloudEngine(client).generate_answer("Two plus two?", "", on_status=statuses.append, on_text=text.append)
        assert answer == "Four"
        assert text == ["Four"] and statuses == [3, 1, None]
        assert calls == [("GET", "/cloud/capabilities"), ("POST", "/answer")]
        assert client.users == 0
