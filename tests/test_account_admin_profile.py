"""Account names and owner maintenance actions against an isolated SQLite database."""
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from server.api import create_app
from server.admin import audit_cutoff
from server.store import Store

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def prepared(tmp_path, monkeypatch):
    path = tmp_path / "backend.sqlite"
    store = Store(path)
    with store.transaction() as tx:
        tokens = {sub: store.issue(tx, sub, sub + "-device") for sub in ("owner", "qa", "real")}
        tx.execute("INSERT INTO profile VALUES('owner','owner@gmail.com')")
        tx.execute("INSERT INTO profile VALUES('qa','qa-332@example.test')")
        tx.execute("INSERT INTO profile VALUES('real','real@gmail.com')")
    monkeypatch.setenv("EXAMAI_ADMIN_SUBJECTS", "owner")
    client = TestClient(create_app(path))
    headers = {sub: {"Authorization": "Bearer " + token} for sub, token in tokens.items()}
    return client, store, headers


def test_name_is_private_persistent_and_survives_google_profile_update(tmp_path, monkeypatch):
    client, store, headers = prepared(tmp_path, monkeypatch)
    assert client.put("/account/name", headers=headers["qa"], json={"name": "  Олена   Петренко  "}).json() == {
        "name": "Олена Петренко"}
    assert client.get("/account", headers=headers["qa"]).json()["name"] == "Олена Петренко"
    assert client.get("/account", headers=headers["real"]).json()["name"] == ""
    assert client.put("/account/name", json={"name": "Інший"}).status_code == 401
    assert client.put("/account/name", headers=headers["qa"], json={"name": " "}).status_code == 422
    with store.transaction() as tx:
        store.observe_google(tx, {"sub": "qa", "email": "qa-332@example.test",
                                  "email_verified": True, "hd": "example.test"}, "unused")
    assert TestClient(create_app(store.path)).get("/account", headers=headers["qa"]).json()["name"] == "Олена Петренко"


def test_startup_opens_google_login_with_name_field(tmp_path):
    from PySide6.QtWidgets import QApplication
    from examai.config import ConfigManager
    from examai.database import DatabaseManager
    from examai.ui.ui_main import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(ConfigManager(tmp_path / "config.json"),
                        DatabaseManager(tmp_path / "notes.db"), startup_devices=[])
    window.account_panel.client.url = "http://127.0.0.1:8000"
    window.show_account_on_startup()
    panel = window.account_panel
    assert not window.isVisible()
    assert not window.settings_window.isVisible()
    assert panel.welcome.isVisible()
    assert not panel.welcome.google.isEnabled()
    panel.welcome.name.setText("   ")
    assert not panel.welcome.google.isEnabled()
    panel.welcome.name.setText("Олена")
    assert panel.welcome.google.isEnabled()
    panel.welcome.close()
    window.show()
    window.open_settings()
    settings = window.settings_scrolls["general"].widget()
    from PySide6.QtWidgets import QCheckBox
    assert len(settings.findChildren(QCheckBox)) == 3
    assert settings.isAncestorOf(panel.invite)
    assert not settings.isAncestorOf(panel.name_input)
    window.settings_window.hide()
    window.hide()
    app.processEvents()


def test_users_search_by_unicode_name_and_returns_real_sorted_usage(tmp_path, monkeypatch):
    client, store, headers = prepared(tmp_path, monkeypatch)
    assert client.put("/account/name", headers=headers["qa"], json={"name": "Ївга"}).status_code == 200
    assert client.put("/account/name", headers=headers["qa"], json={"name": "Ім’я\u202e"}).status_code == 422
    with store.transaction() as tx:
        day = int(store.now() // 86400)
        tx.execute("INSERT INTO usage(sub,day,requests) VALUES(?,?,?)", ("qa", day, 17))
        for index in range(27):
            sub = f"person-{index}"
            store.issue(tx, sub, "pagination")
            tx.execute("INSERT INTO profile VALUES(?,?)", (sub, sub + "@example.test"))
    route = "/admin/v2/overview"
    assert client.post(route, headers=headers["real"], json={}).status_code == 403
    first = client.post(route, headers=headers["owner"],
                        json={"sort": "requests", "descending": True}).json()
    assert first["user_pagination"] == {"page": 1, "pages": 2, "total": 30}
    assert first["users"][0]["requests"] == 17
    second = client.post(route, headers=headers["owner"], json={"page": 2}).json()
    assert len(second["users"]) == 5
    match = client.post(route, headers=headers["owner"], json={"search": "їВГА"}).json()
    assert [user["name"] for user in match["users"]] == ["Ївга"]


def test_owner_can_grant_real_unlimited_access(tmp_path, monkeypatch):
    client, _, headers = prepared(tmp_path, monkeypatch)
    body = {"email": "owner@gmail.com", "action": "unlimited", "amount": 0,
            "operation_id": str(uuid4())}
    assert client.post("/admin/v2/licenses", headers=headers["qa"], json=body).status_code == 403
    assert client.post("/admin/v2/licenses", headers=headers["owner"], json=body).status_code == 200
    account = client.get("/account", headers=headers["owner"]).json()
    assert account["state"] == "ACTIVE" and account["until"] is None
    overview = client.post("/admin/v2/overview", headers=headers["owner"], json={}).json()
    assert next(row for row in overview["users"] if row["email"] == "owner@gmail.com")["license"] == "Unlimited"


def test_owner_purge_removes_only_test_accounts_and_related_data(tmp_path, monkeypatch):
    client, store, headers = prepared(tmp_path, monkeypatch)
    with store.transaction() as tx:
        tx.execute("INSERT INTO report(sub,kind,description,created) VALUES('qa','Bug','QA',?)", (time.time(),))
        report_id = tx.execute("SELECT id FROM report WHERE sub='qa'").fetchone()["id"]
        tx.execute("INSERT INTO report_attachment VALUES(?,?,?)", (report_id, "screenshot", b"image"))
        tx.execute("INSERT INTO beta_invitation(id,code,email,sub,kind,amount,created,expires) "
                   "VALUES('qa-invite','hash','qa2@example.test','real','minutes',30,?,?)", (time.time(), time.time()+1000))
        store.audit(tx, "owner", "qa", "QA action")
        store.audit(tx, "owner", "real", "Real action")
    assert client.get("/admin/v2/qa/preview", headers=headers["qa"]).status_code == 403
    assert client.get("/admin/v2/qa/preview", headers=headers["owner"]).json() == {
        "accounts": 1, "invitations": 1}
    assert client.post("/admin/v2/qa/purge", headers=headers["qa"], json={"confirmed": True}).status_code == 403
    assert client.post("/admin/v2/qa/purge", headers=headers["owner"], json={"confirmed": False}).status_code == 422
    result = client.post("/admin/v2/qa/purge", headers=headers["owner"], json={"confirmed": True})
    assert result.status_code == 200 and result.json() == {"accounts": 1, "invitations": 1}
    assert client.get("/account", headers=headers["qa"]).status_code == 401
    assert client.get("/account", headers=headers["real"]).status_code == 200
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM report_attachment").fetchone()[0] == 0
        assert tx.execute("SELECT count(*) FROM beta_invitation").fetchone()[0] == 0
        assert tx.execute("SELECT count(*) FROM audit WHERE subject='qa'").fetchone()[0] == 1
        assert tx.execute("SELECT count(*) FROM audit WHERE subject='real'").fetchone()[0] == 1
        assert tx.execute("SELECT count(*) FROM profile WHERE sub='qa'").fetchone()[0] == 0


def test_qa_cleanup_refuses_to_remove_an_owner(tmp_path, monkeypatch):
    client, store, headers = prepared(tmp_path, monkeypatch)
    with store.transaction() as tx:
        tx.execute("INSERT INTO staff VALUES('qa','Owner')")
    response = client.post("/admin/v2/qa/purge", headers=headers["owner"], json={"confirmed": True})
    assert response.status_code == 409
    assert client.get("/account", headers=headers["qa"]).status_code == 200


def test_audit_clear_uses_selected_window_and_preserves_cleanup_history(tmp_path, monkeypatch):
    client, store, headers = prepared(tmp_path, monkeypatch)
    now = time.time()
    with store.transaction() as tx:
        tx.execute("INSERT INTO audit(actor,subject,action,created) VALUES('owner','real','old',?)", (now-700,))
        tx.execute("INSERT INTO audit(actor,subject,action,created) VALUES('owner','real','recent',?)", (now-20,))
        tx.execute("INSERT INTO audit(actor,subject,action,created) VALUES('owner','owner',"
                   "'Purged example.test QA data: 0 accounts',?)", (now-10,))
    body = {"period": "5m", "confirmed": True}
    assert client.get("/admin/v2/audit/preview?period=5m", headers=headers["real"]).status_code == 403
    assert client.get("/admin/v2/audit/preview?period=5m", headers=headers["owner"]).json() == {
        "count": 1}
    assert client.post("/admin/v2/audit/clear", headers=headers["real"], json=body).status_code == 403
    assert client.post("/admin/v2/audit/clear", headers=headers["owner"],
                       json={"period": "5m", "confirmed": False}).status_code == 422
    assert client.post("/admin/v2/audit/clear", headers=headers["owner"],
                       json={"period": "2y", "confirmed": True}).status_code == 422
    assert client.post("/admin/v2/audit/clear", headers=headers["owner"], json=body).json() == {"deleted": 1}
    with store.transaction() as tx:
        actions = [row[0] for row in tx.execute("SELECT action FROM audit ORDER BY id")]
    assert "old" in actions and "recent" in actions
    assert client.post("/admin/v2/audit/clear", headers=headers["owner"], json=body).json() == {"deleted": 0}
    visible = client.post("/admin/v2/overview", headers=headers["owner"], json={}).json()["audit"]
    assert [entry["action"] for entry in visible] == ["old"]
    assert any(action.startswith("Purged example.test") for action in actions)
    assert any(action.startswith("Cleared audit 5m") for action in actions)


def test_month_and_year_windows_follow_calendar_dates():
    march = datetime(2026, 3, 31, 12, tzinfo=timezone.utc).timestamp()
    leap = datetime(2024, 2, 29, 12, tzinfo=timezone.utc).timestamp()
    assert audit_cutoff(march, "1mo") == datetime(2026, 2, 28, 12, tzinfo=timezone.utc).timestamp()
    assert audit_cutoff(leap, "1y") == datetime(2023, 2, 28, 12, tzinfo=timezone.utc).timestamp()


def test_admin_cookie_survives_reload_and_requires_origin_for_writes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.store import digest

    origin = "https://examai.example"
    monkeypatch.setenv("EXAMAI_PUBLIC_URL", origin)
    client, store, headers = prepared(tmp_path, monkeypatch)
    with store.transaction() as tx:
        store.issue(tx, "owner", "second-desktop")
        tx.execute("INSERT INTO oauth VALUES(?,?,?,?,?,?,?)",
                   ("callback", "", "nonce", digest("q" * 32), "admin-cookie:", store.now() + 60, "owner"))
    browser = TestClient(client.app, base_url=origin)
    csrf = {"Origin": origin, "X-ExamAI-Request": "admin"}
    assert browser.post("/oauth/claim", json={"claim": "q" * 32}).status_code == 410
    cookie = browser.post("/admin/session/claim", headers=csrf, json={"claim": "q" * 32})
    assert cookie.status_code == 200 and cookie.json() == {"ok": True}
    assert all(flag in cookie.headers["set-cookie"].lower() for flag in
               ("httponly", "secure", "samesite=strict", "max-age=", "path=/"))
    assert browser.get("/admin#users").status_code == 200
    assert browser.get("/admin/session").json()["role"] == "Owner"
    assert browser.post("/admin/v2/overview", json={}).status_code == 403
    assert browser.post("/admin/v2/overview", headers=csrf, json={}).status_code == 200
    assert browser.post("/admin/v2/audit/clear", headers=csrf | {"Origin": "https://other.example"},
                        json={"period": "1d", "confirmed": True}).status_code == 403
    assert browser.get("/account").status_code == 401
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM session WHERE sub='owner'").fetchone()[0] == 2
    assert browser.post("/admin/session/logout", headers=csrf).status_code == 200
    assert browser.get("/admin/session").status_code == 401
    assert client.get("/account", headers=headers["owner"]).status_code == 200


def test_desktop_recovery_keeps_active_other_device_and_reuses_existing_id(tmp_path):
    import pytest
    from server.store import Denied, digest

    store = Store(tmp_path / "devices.sqlite")
    with store.transaction() as tx:
        first = store.issue(tx, "person", "first-device")
        oldest_idle = store.issue(tx, "person", "second-device")
        tx.execute("UPDATE account SET state='BETA',remaining=300 WHERE sub='person'")
        assert store.access(tx, "person", token=digest(first), lease_action="start")["granted"]
    with pytest.raises(Denied, match="Device limit"):
        with store.transaction() as tx:
            store.issue(tx, "person", "third-device")
    with store.transaction() as tx:
        replacement = store.issue(tx, "person", "third-device", replace=True)
        assert store.authenticate(tx, first) == "person"
        with pytest.raises(Denied):
            store.authenticate(tx, oldest_idle)
        assert not store.access(tx, "person", token=digest(replacement), lease_action="start")["granted"]
        assert store.issue(tx, "person", "third-device") != replacement
        assert tx.execute("SELECT count(*) FROM session WHERE sub='person'").fetchone()[0] == 2


def test_selected_deletion_backs_up_and_blocks_recreating_account(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    client, store, headers = prepared(tmp_path, monkeypatch)
    path = "/admin/v2/users/maintenance"
    body = {"subjects": ["qa"], "action": "delete", "confirmed": True}
    assert client.post(path, headers=headers["real"], json=body).status_code == 403
    assert client.post(path, headers=headers["owner"], json=body | {"subjects": ["owner"]}).status_code == 403
    assert client.post(path, headers=headers["owner"], json=body | {"action": "disable"}).status_code == 200
    assert client.get("/account", headers=headers["qa"]).status_code == 401
    assert client.post(path, headers=headers["owner"], json=body).json() == {"count": 1}
    backup = next((tmp_path / "deletion-backups").glob("*.sqlite"))
    with closing(sqlite3.connect(backup)) as original:
        assert original.execute("SELECT count(*) FROM account WHERE sub='qa'").fetchone()[0] == 1
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM account WHERE sub='qa'").fetchone()[0] == 0
        assert tx.execute("SELECT count(*) FROM disabled_account WHERE sub='qa'").fetchone()[0] == 1
        assert tx.execute("SELECT count(*) FROM audit WHERE subject='qa'").fetchone()[0] == 2


def test_windows_switch_scopes_notes_history_and_keys(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from examai.app import MainController
    from examai.config import ConfigManager
    from examai.database import DatabaseManager

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("examai.app.keyboard.add_hotkey", lambda *_: None)
    config = ConfigManager(tmp_path / "config.json")
    database = DatabaseManager(tmp_path / "legacy.sqlite")
    controller = MainController(app, config=config, database=database, startup_devices=[])
    panel = controller.ui.account_panel
    config.set("gemini_api_key", "legacy key")
    controller.ui.input_manual_question.setText("legacy draft")
    panel.activate_profile({"state": "EXPIRED", "seconds": 0, "until": None, "profile_id": "first"})
    first = controller.db.db_path
    assert not controller.ui.input_manual_question.text()
    assert not config.get("gemini_api_key")
    config.set("gemini_api_key", "first key")
    controller.db.save_qa("private question", "private answer", "test", session="test", account=panel.profile_id)
    panel.activate_profile({"state": "EXPIRED", "seconds": 0, "until": None, "profile_id": "second"})
    assert controller.db.db_path != first
    assert not config.get("gemini_api_key")
    assert not controller._answer_history
    panel.activate_profile({"state": "EXPIRED", "seconds": 0, "until": None, "profile_id": "first"})
    assert controller.db.db_path == first
    assert controller._answer_history[0][1:] == ("private question", "private answer")
    panel.balance_timer.stop()
    app.aboutToQuit.disconnect(controller.shutdown)
    controller.shutdown()
