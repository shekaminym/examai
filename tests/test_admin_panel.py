"""Exercise the administration workflow through the actual API and SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import sqlite3
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import base64
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.api import create_app
from server.store import Store, digest


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def beta(tmp_path, monkeypatch):
    path = tmp_path / "beta.sqlite"
    store = Store(path)
    with store.transaction() as tx:
        owner = auth(store.issue(tx, "owner", "initial-device"))
        user = auth(store.issue(tx, "other", "initial-device"))
        tx.execute("INSERT INTO profile VALUES('owner','owner@gmail.com')")
    monkeypatch.setenv("EXAMAI_ADMIN_SUBJECTS", "owner")
    monkeypatch.setenv("EXAMAI_PUBLIC_URL", "https://beta.example.test")
    monkeypatch.setenv("EXAMAI_GOOGLE_CLIENT_ID", "oauth-fixture")
    monkeypatch.setenv("EXAMAI_GOOGLE_CLIENT_SECRET", "secret-fixture")
    client = TestClient(create_app(path))
    return client, store, owner, user, monkeypatch


def sign_in(beta, sub, email, *, verified=True, domain=None):
    client, _, _, _, monkeypatch = beta
    flow = client.post("/oauth/start", json={"device": str(uuid4())}).json()
    query = parse_qs(urlsplit(flow["url"]).query)
    monkeypatch.setattr("server.api.httpx.post", lambda url, **kwargs: httpx.Response(
        200, json={"id_token": "fixture"}, request=httpx.Request("POST", url)))
    monkeypatch.setattr("server.api.verify_oauth2_token", lambda *_: dict(
        sub=sub, email=email, email_verified=verified, hd=domain, nonce=query["nonce"][0]))
    assert client.get("/oauth/callback", params={"state": query["state"][0], "code": "fixture"}).is_success
    result = client.post("/oauth/claim", json={"claim": flow["claim"]})
    return auth(result.json()["token"])


def new_invite(client, owner, email, kind="minutes", amount=30):
    payload = dict(email=email, kind=kind, amount=amount, validity=7, operation_id=str(uuid4()))
    response = client.post("/admin/v2/invitations", headers=owner, json=payload)
    assert response.is_success, response.text
    assert client.post("/admin/v2/invitations", headers=owner, json=payload).json() == response.json()
    return response.json()


def change(client, actor, email, action, amount=0):
    return client.post("/admin/v2/licenses", headers=actor, json=dict(
        email=email, action=action, amount=amount, operation_id=str(uuid4())))


def test_group_invitation_atomic_capacity_duplicate_and_individual_balance(beta):
    client, store, owner, outsider, _ = beta
    route = "/admin/v2/group-invitations"
    body = dict(label="Тестова група", max_uses=3, validity=7, operation_id=str(uuid4()))
    assert client.post(route, headers=outsider, json=body).status_code == 403
    created = client.post(route, headers=owner, json=body).json()
    assert client.post(route, headers=owner, json=body).json() == created
    item = route + "/" + created["id"]
    code = created["code"]
    assert client.get(item + "/code", headers=owner).json() == {"code": code}
    assert client.get(item, headers=outsider).status_code == 403
    assert client.get(item + "/code", headers=outsider).status_code == 403
    assert client.delete(item, headers=outsider).status_code == 403
    views = client.post("/admin/v2/overview", headers=owner, json={}).json()
    assert views["group_invitations"][0]["uses"] == 0
    assert code not in str(views)

    users = [sign_in(beta, f"group-{i}", f"group{i}@gmail.com") for i in range(4)]
    unverified = sign_in(beta, "unverified-group", "unverified-group@gmail.com", verified=False)

    def redeem(account):
        return client.post("/invite", headers=account, json={"code": code})

    assert redeem(unverified).status_code == 409
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(redeem, [users[0], users[0], users[1], users[2], users[3]]))
    assert sorted(result.status_code for result in results) == [200, 200, 200, 409, 409]
    assert all(result.json()["seconds"] == 1800 for result in results if result.is_success)
    balances = [client.get("/account", headers=account).json()["seconds"] for account in users]
    assert sorted(balances) == [0, 1800, 1800, 1800]
    assert all(redeem(account).status_code == 409 for account in users)
    details = client.get(item, headers=owner).json()
    assert details["uses"] == 3 and details["status"] == "Exhausted"
    assert len({entry["email"] for entry in details["activations"]}) == 3
    assert client.get(item + "/code", headers=owner).status_code == 409
    assert client.delete(item, headers=owner).is_success
    assert client.get(item, headers=owner).json()["status"] == "Revoked"
    assert [client.get("/account", headers=account).json()["seconds"] for account in users] == balances


def test_group_invite_expiry_and_existing_invites(beta):
    client, store, owner, _, _ = beta
    route = "/admin/v2/group-invitations"
    body = dict(label="Інша тестова група", max_uses=3, validity=1, operation_id=str(uuid4()))
    first = client.post(route, headers=owner, json=body).json()
    expired = client.post(route, headers=owner, json={**body, "operation_id": str(uuid4())}).json()
    aid = sign_in(beta, "prior", "prior@gmail.com")
    bid = sign_in(beta, "new", "new@gmail.com")
    former = new_invite(client, owner, "prior@gmail.com")
    def redeem(account, invite):
        return client.post("/invite", headers=account, json={"code": invite["code"]})
    assert redeem(aid, former).json()["seconds"] == 1800
    assert redeem(aid, first).status_code == 409
    assert redeem(bid, first).json()["seconds"] == 1800
    second = new_invite(client, owner, "new@gmail.com")
    assert redeem(bid, second).status_code == 409
    with store.transaction() as tx:
        tx.execute("UPDATE beta_group_invitation SET expires=? WHERE id=?", (store.now(), expired["id"]))
    cid = sign_in(beta, "fresh", "fresh@gmail.com")
    assert redeem(cid, expired).status_code == 409
    assert client.get(route + "/" + expired["id"], headers=owner).json()["status"] == "Expired"


def test_launch_wave_on_migrated_database_and_restore(beta, tmp_path):
    client, store, owner, _, _ = beta
    route = "/admin/v2/group-invitations"
    with closing(sqlite3.connect(store.path)) as tx:
        assert tx.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    client = TestClient(create_app(store.path))
    with closing(sqlite3.connect(store.path)) as tx:
        assert tx.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    payload = dict(label="QA wave", max_uses=25, validity=2, operation_id=str(uuid4()))
    invite = client.post(route, headers=owner, json=payload).json()
    with store.transaction() as tx:
        tokens = []
        for i in range(26):
            sub = f"wave-{i}"
            tokens.append(auth(store.issue(tx, sub, "device")))
            tx.execute("INSERT INTO google_identity VALUES(?,?,?)", (sub, f"{sub}@example.test", store.now()))
        payer = auth(store.issue(tx, "metering", "device"))
        tx.execute("UPDATE account SET state='BETA',remaining=1800 WHERE sub='metering'")
        tx.execute("INSERT INTO lease VALUES(?,?,?,?)",
                   ("metering", digest(payer["Authorization"][7:]), store.now() - 3, store.now() + 10))
        tx.execute("INSERT INTO report(sub,kind,description,created) VALUES(?,?,?,?)",
                   ("metering", "Bug", "restore fixture", store.now()))
    gate = Barrier(29)

    def redeem(headers):
        gate.wait()
        response = client.post("/invite", headers=headers, json={"code": invite["code"]})
        return response.status_code

    def meter():
        gate.wait()
        return client.post("/activity/stop", headers=payer).json()["seconds"]

    with ThreadPoolExecutor(max_workers=29) as pool:
        futures = [pool.submit(redeem, entry) for entry in [*tokens, tokens[0], tokens[1]]]
        debit = pool.submit(meter)
        results = [entry.result(timeout=20) for entry in futures]
        assert 0 < debit.result(timeout=20) < 1800
    assert sorted(results) == [200] * 25 + [409] * 3
    item = route + "/" + invite["id"]
    assert client.delete(item, headers=owner).status_code == 200
    backup = tmp_path / "restored.sqlite"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restarted = TestClient(create_app(backup))
    details = restarted.get(item, headers=owner).json()
    assert details["uses"] == 25 and details["status"] == "Revoked"
    checks = [restarted.get("/account", headers=user).json()["seconds"] for user in tokens]
    assert sorted(checks) == [0] + [1800] * 25
    assert restarted.get("/reports", headers=payer).json()[0]["kind"] == "Bug"
    assert restarted.post("/invite", headers=tokens[0], json={"code": invite["code"]}).status_code == 409


def test_invite_and_pending_admin_are_pinned_to_verified_google_subject(beta):
    client, store, owner, outsider, monkeypatch = beta
    email = "tester@gmail.com"
    assert client.post("/admin/v2/overview", headers=outsider, json={}).status_code == 403
    assert client.post("/admin/v2/overview", cookies={"session": "unused"}, json={}).status_code == 401
    assert client.post("/admin/v2/staff", headers=owner, json={"email": email}).json() == {"role": "Pending"}
    invite = new_invite(client, owner, email)
    route = f"/admin/v2/invitations/{invite['id']}/code"
    assert client.get(route, headers=owner).json()["code"] == invite["code"]
    body = {"code": invite["code"]}
    unverified = sign_in(beta, "unverified", email, verified=False)
    assert client.post("/invite", headers=unverified, json=body).status_code == 409
    assert client.post("/admin/v2/overview", headers=unverified, json={}).status_code == 403
    admin = sign_in(beta, "verified", email.upper())
    assert client.post("/admin/v2/overview", headers=admin, json={}).json()["role"] == "Admin"
    assert client.post("/invite", headers=admin, json=body).json()["seconds"] == 1800
    assert client.post("/invite", headers=admin, json=body).status_code == 409
    assert client.get(route, headers=owner).status_code == 409
    assert client.post("/admin/v2/staff", headers=admin, json={"email": "third@gmail.com"}).status_code == 403
    assert change(client, admin, "owner@gmail.com", "suspend").status_code == 403
    assert client.post("/admin/action", headers=admin, json=dict(sub="owner", action="suspend")).status_code == 403
    assert client.request("DELETE", "/admin/v2/staff", headers=owner, json={"subject": "owner"}).status_code == 409
    renamed = sign_in(beta, "verified", "new-address@gmail.com")
    assert client.post("/admin/v2/overview", headers=renamed, json={"search": "new-address"}).json()["users"][0]["role"] == "Admin"
    assert client.request("DELETE", "/admin/v2/staff", headers=owner, json={"subject": "verified"}).is_success
    assert client.post("/admin/v2/overview", headers=admin, json={}).status_code == 401
    assert client.post("/admin/v2/overview", headers=renamed, json={}).status_code == 401
    assert change(client, owner, "new-address@gmail.com", "remove", 60).is_success
    reissued = sign_in(beta, "verified", "new-address@gmail.com")
    assert client.get("/account", headers=reissued).json()["seconds"] == 0
    monkeypatch.setenv("EXAMAI_ADMIN_SUBJECTS", "owner,verified")
    assert TestClient(create_app(store.path)).post("/admin/v2/overview", headers=reissued, json={}).status_code == 403
    actions = client.post("/admin/v2/overview", headers=owner, json={}).json()["audit"]
    assert {"Activated administrator", "Revoked administrator", "License remove 60"} <= {entry["action"] for entry in actions}


@pytest.mark.parametrize("kind,amount", [("days", 7), ("unlimited", 0)])
def test_calendar_license_survives_compatibility_grants(beta, kind, amount):
    client, store, owner, _, _ = beta
    email = "calendar@gmail.com"
    user = sign_in(beta, "calendar", email)
    invite = new_invite(client, owner, email, kind, amount)
    state = client.post("/invite", headers=user, json={"code": invite["code"]}).json()
    assert state["seconds"] == 0 and state["state"] == "ACTIVE"
    if kind == "days":
        assert 604799 <= state["until"] - store.now() <= 604800
    else:
        assert state["until"] is None
    assert client.post("/admin/action", headers=owner, json=dict(
        sub="calendar", action="minutes", amount=10, operation_id=str(uuid4()))).is_success
    bonus = client.get("/account", headers=user).json()
    assert bonus["state"] == "ACTIVE" and bonus["until"] == state["until"]
    assert change(client, owner, email, "revoke").is_success
    assert not client.get("/account", headers=user).json()["granted"]


def test_revoked_invites_and_report_assignment_with_bonus(beta):
    client, store, owner, _, _ = beta
    email = "reporter@gmail.com"
    user = sign_in(beta, "reporter", email)
    invitation = new_invite(client, owner, email)
    assert client.delete("/admin/v2/invitations/" + invitation["id"], headers=owner).is_success
    assert client.post("/invite", headers=user, json={"code": invitation["code"]}).status_code == 409
    png = BytesIO()
    Image.new("RGB", (2, 2)).save(png, "PNG")
    report = dict(kind="Bug", description="Screen capture issue")
    malformed = base64.b64encode(b"\x89PNG\r\n\x1a\ninvalid").decode()
    assert client.post("/reports", headers=user, json={**report, "screenshot": malformed}).status_code == 422
    report_id = client.post("/reports", headers=user, json={
        **report, "screenshot": base64.b64encode(png.getvalue()).decode()}).json()["id"]
    route = f"/admin/reports/{report_id}/screenshot"
    assert client.get(route, headers=user).status_code == 403
    assert client.get(route).status_code == 401
    with Image.open(BytesIO(client.get(route, headers=owner).content)) as saved:
        assert saved.format == "PNG" and saved.size == (2, 2)
        assert saved.getpixel((0, 0)) == (0, 0, 0)
    action = dict(status="Reviewing", assignee="owner@gmail.com", bonus=10, operation_id=str(uuid4()))
    for _ in range(2):
        assert client.post(f"/admin/v2/reports/{report_id}", headers=owner, json=action).is_success
    assert client.get("/account", headers=user).json()["seconds"] == 600
    response = client.post("/admin/v2/overview", headers=owner, json={"status": "Reviewing"})
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["reports"][0]["assignee"] == "owner@gmail.com"
    assert any(entry["action"].startswith(f"Report #{report_id}:") for entry in response.json()["audit"])
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM beta_invitation WHERE code=?", (digest(invitation["code"]),)).fetchone()[0] == 1


def test_owner_can_promote_an_existing_verified_account(beta):
    client, _, owner, _, _ = beta
    email = "existing@workspace.example"
    external = sign_in(beta, "other-address", email)
    assert client.post("/admin/v2/staff", headers=owner, json={"email": email}).json() == {"role": "Pending"}
    assert client.post("/admin/v2/overview", headers=external, json={}).status_code == 403
    trusted = sign_in(beta, "workspace", email, domain="workspace.example")
    assert client.post("/admin/v2/overview", headers=trusted, json={}).json()["role"] == "Admin"
    gmail = sign_in(beta, "gmail", "existing@gmail.com")
    assert client.post("/admin/v2/staff", headers=owner, json={"email": "existing@gmail.com"}).json()["role"] == "Admin"
    assert client.post("/admin/v2/overview", headers=gmail, json={}).status_code == 401
    assert client.post("/admin/v2/overview", headers=sign_in(beta, "gmail", "existing@gmail.com"),
                       json={}).json()["role"] == "Admin"


def test_shared_owner_requires_confirmed_exact_identity_and_preserves_last_owner(beta):
    client, _, owner, outsider, _ = beta
    email = "second-owner@gmail.com"
    member = sign_in(beta, "second", email)
    assert client.post("/admin/v2/staff", headers=owner, json={"email": email}).is_success
    member = sign_in(beta, "second", email)
    body = {"subject": "second", "email": email, "role": "Owner", "confirmed": True}
    endpoint = "/admin/v2/staff"
    assert client.patch(endpoint, headers=outsider, json=body).status_code == 403
    assert client.patch(endpoint, headers=member, json=body).status_code == 403
    assert client.patch(endpoint, headers=owner, json={**body, "confirmed": False}).status_code == 422
    assert client.patch(endpoint, headers=owner, json={**body, "subject": "other"}).status_code == 409
    assert client.patch(endpoint, headers=owner, json=body).json() == {"role": "Owner"}
    assert client.post("/admin/v2/overview", headers=member, json={}).status_code == 401
    member = sign_in(beta, "second", email)
    assert client.post("/admin/v2/overview", headers=member, json={}).json()["role"] == "Owner"
    for actor in (owner, member):
        invite = new_invite(client, actor, "beta-guest@gmail.com", "unlimited", 0)
        assert client.get(f"/admin/v2/invitations/{invite['id']}/code", headers=actor).is_success
    assert client.patch(endpoint, headers=member, json={**body, "role": "Admin"}).is_success
    last = {**body, "email": "owner@gmail.com", "subject": "owner", "role": "Admin"}
    assert client.patch(endpoint, headers=owner, json=last).status_code == 409


@pytest.mark.parametrize("kind,amount", [("minutes", 30), ("minutes", 60), ("days", 7), ("days", 30), ("unlimited", None)])
def test_invite_duration_and_safe_validation(beta, kind, amount):
    client, store, owner, _, _ = beta
    email = "invite-check@gmail.com"
    user = sign_in(beta, "recipient", email)
    payload = dict(email=email, kind=kind, validity=1, operation_id=str(uuid4()))
    if amount is not None:
        payload["amount"] = amount
    invite = client.post("/admin/v2/invitations", headers=owner, json=payload)
    assert invite.is_success, invite.text
    response = client.post("/invite", headers=user, json={"code": "short"})
    assert response.status_code == 422
    assert "short" not in response.text and isinstance(response.json()["detail"], str)
    response = client.post("/invite", headers=user, json={"code": invite.json()["code"]})
    assert response.is_success
    grant = response.json()
    if kind == "days":
        assert amount * 86400 - 1 <= grant["until"] - store.now() <= amount * 86400
    elif kind == "minutes":
        assert grant["seconds"] == amount * 60
    else:
        assert grant["state"] == "ACTIVE" and grant["until"] is None


@pytest.mark.parametrize("source_format", ["PNG", "JPEG", "WEBP", "BMP", "GIF"])
def test_report_attachments_normalize_actual_format_and_protect_download(beta, source_format):
    client, _store, owner, _user, _monkeypatch = beta
    user = sign_in(beta, "format-test", "format-test@gmail.com")
    blob = BytesIO()
    frames = {"save_all": True, "append_images": [Image.new("RGB", (12, 8), "blue")]} if source_format == "GIF" else {}
    Image.new("RGB", (12, 8), "red").save(blob, source_format, **frames)
    payload = {"kind": "Bug", "description": "Report attachment format",
               "screenshot": base64.b64encode(blob.getvalue()).decode()}
    assert client.post("/reports", json=payload).status_code == 401
    response = client.post("/reports", headers=user, json=payload)
    assert response.status_code == 200
    route = f"/admin/reports/{response.json()['id']}/screenshot"
    assert client.get(route, headers=user).status_code == 403
    with Image.open(BytesIO(client.get(route, headers=owner).content)) as image:
        assert image.format == "PNG" and image.size == (12, 8)
        assert image.n_frames == 1
        red, green, blue = image.getpixel((0, 0))
        assert red > 240 and green < 10 and blue < 10


def test_report_rejects_image_dimensions_and_unapproved_codec():
    from examai.report_images import normalize_report_image

    for size, fmt in [((4097, 1), "PNG"), ((1, 1), "TIFF")]:
        blob = BytesIO()
        Image.new("RGB", size).save(blob, fmt)
        with pytest.raises(ValueError):
            normalize_report_image(blob.getvalue())
