"""Transactional beta accounts and usage. Times come from the server."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager

BETA_SECONDS = 1800
LEASE_SECONDS = 15
SESSION_SECONDS = 12 * 3600
REQUESTS_PER_DAY = 30
CLOUD_UNITS_PER_DAY = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS account (sub TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'EXPIRED',
    remaining REAL NOT NULL DEFAULT 0, until REAL, invited INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS session (token TEXT PRIMARY KEY, sub TEXT NOT NULL, device TEXT NOT NULL, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invite (code TEXT PRIMARY KEY, sub TEXT, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS lease (sub TEXT PRIMARY KEY, token TEXT NOT NULL, last REAL NOT NULL, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (sub TEXT, day INTEGER, requests INTEGER, units INTEGER, PRIMARY KEY(sub, day));
CREATE TABLE IF NOT EXISTS report (id INTEGER PRIMARY KEY, sub TEXT, kind TEXT, description TEXT,
    status TEXT NOT NULL DEFAULT 'Received', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, subject TEXT, action TEXT, created REAL);
CREATE TABLE IF NOT EXISTS oauth (state TEXT PRIMARY KEY, verifier TEXT, nonce TEXT, claim TEXT,
    device TEXT, until REAL, sub TEXT);
"""


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Denied(ValueError):
    pass


class Store:
    def __init__(self, path, now=time.time):
        self.path = path
        self.now = now
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            yield db

    def issue(self, db, sub, device):
        now = self.now()
        db.execute("INSERT OR IGNORE INTO account(sub) VALUES(?)", (sub,))
        db.execute("DELETE FROM session WHERE until <= ? OR (sub=? AND device=?)", (now, sub, device))
        if db.execute("SELECT count(*) FROM session WHERE sub=?", (sub,)).fetchone()[0] >= 2:
            raise Denied("Device limit reached")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO session VALUES(?,?,?,?)", (digest(token), sub, device, now + SESSION_SECONDS))
        return token

    def authenticate(self, db, token):
        row = db.execute("SELECT sub FROM session WHERE token=? AND until>?",
                         (digest(token), self.now())).fetchone()
        if row is None:
            raise Denied("Session expired")
        return row["sub"]

    def redeem(self, db, sub, token):
        row = db.execute("SELECT sub, used FROM invite WHERE code=?", (digest(token),)).fetchone()
        account = db.execute("SELECT invited, state FROM account WHERE sub=?", (sub,)).fetchone()
        if (not row or row["used"] or row["sub"] not in (sub, None)
                or account["invited"] or account["state"] == "SUSPENDED"):
            raise Denied("Invite unavailable")
        db.execute("UPDATE invite SET used=1 WHERE code=?", (digest(token),))
        db.execute("UPDATE account SET invited=1, state='BETA', remaining=?, until=NULL WHERE sub=?",
                   (BETA_SECONDS, sub))
        self.audit(db, sub, sub, "redeemed beta invite")

    def access(self, db, sub, *, token=None, lease_action=None, units=0):
        now = self.now()
        account = db.execute("SELECT * FROM account WHERE sub=?", (sub,)).fetchone()
        lease = db.execute("SELECT * FROM lease WHERE sub=?", (sub,)).fetchone()
        if lease:
            elapsed = max(0, min(now, lease["until"]) - lease["last"])
            if account["state"] in {"BETA", "TRIAL"} and elapsed:
                db.execute("UPDATE account SET remaining=max(0,remaining-?) WHERE sub=?", (elapsed, sub))
            if now >= lease["until"] or (lease_action == "stop" and lease["token"] == token):
                db.execute("DELETE FROM lease WHERE sub=?", (sub,))
                lease = None
            else:
                db.execute("UPDATE lease SET last=? WHERE sub=?", (now, sub))
        account = dict(db.execute("SELECT * FROM account WHERE sub=?", (sub,)).fetchone())
        state = account["state"]
        allowed = state in {"BETA","TRIAL","ACTIVE"} and (account["until"] is None or now < account["until"])
        allowed = allowed and (state == "ACTIVE" or account["remaining"] > 0)
        occupied = lease is not None and lease["token"] != token
        granted = bool(allowed and not occupied)
        if lease_action == "start" and granted:
            duration = LEASE_SECONDS if state == "ACTIVE" else min(LEASE_SECONDS, account["remaining"])
            if account["until"] is not None:
                duration = min(duration, account["until"] - now)
            db.execute("INSERT OR REPLACE INTO lease VALUES (?,?,?,?)", (sub, token, now, now + duration))
        if units and granted:
            day = int(now // 86400)
            usage = db.execute("SELECT requests,units FROM usage WHERE sub=? AND day=?", (sub, day)).fetchone()
            requests, previous = tuple(usage) if usage else (0, 0)
            total = db.execute("SELECT coalesce(sum(units),0) FROM usage WHERE day=?", (day,)).fetchone()[0]
            if requests >= REQUESTS_PER_DAY or previous + units > CLOUD_UNITS_PER_DAY or total + units > 300:
                granted = False
            else:
                db.execute("INSERT OR REPLACE INTO usage VALUES(?,?,?,?)",
                           (sub, day, requests + 1, previous + units))
        return {"state": state if allowed or state == "SUSPENDED" else "EXPIRED",
                "seconds": int(account["remaining"]), "until": account["until"],
                "granted": granted, "busy": occupied}

    def audit(self, db, actor, subject, action):
        db.execute("INSERT INTO audit(actor,subject,action,created) VALUES(?,?,?,?)",
                   (actor, subject, action, self.now()))
