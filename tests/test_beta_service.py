import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
import pytest

from examai.updates import verify_manifest
from server.api import create_app
from server.payments import signature
from server.store import Store, digest


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
    assert client.post("/admin/action", headers=admin, json={
        "sub": "beta-sub", "action": "minutes", "amount": 10}).is_success
    assert client.get("/account", headers=user).json()["seconds"] == 2400
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


def test_update_manifest_rejects_tampering_and_wrong_channel():
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
