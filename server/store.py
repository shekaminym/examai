"""Transactional beta accounts and usage. Times come from the server."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager

BETA_SECONDS = 1800
ADMIN_INVITATION_SECONDS = 7 * 86400
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
CREATE TABLE IF NOT EXISTS cloud_charge (
    id TEXT PRIMARY KEY, day INTEGER NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, micro_usd INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cloud_charge_day ON cloud_charge(day);
CREATE TABLE IF NOT EXISTS report (id INTEGER PRIMARY KEY, sub TEXT, kind TEXT, description TEXT,
    status TEXT NOT NULL DEFAULT 'Received', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, subject TEXT, action TEXT, created REAL);
CREATE TABLE IF NOT EXISTS oauth (state TEXT PRIMARY KEY, verifier TEXT, nonce TEXT, claim TEXT,
    device TEXT, until REAL, sub TEXT);
CREATE TABLE IF NOT EXISTS profile (sub TEXT PRIMARY KEY, email TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS display_name (sub TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invite_expiry (code TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS suspended_access (sub TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_operation (id TEXT PRIMARY KEY, actor TEXT NOT NULL,
    payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_attachment (report_id INTEGER NOT NULL, kind TEXT NOT NULL,
    content BLOB NOT NULL, PRIMARY KEY(report_id,kind));
CREATE TABLE IF NOT EXISTS staff (sub TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('Owner','Admin')));
CREATE TABLE IF NOT EXISTS staff_bootstrap (id INTEGER PRIMARY KEY CHECK(id=1));
CREATE TABLE IF NOT EXISTS invite_signing (id INTEGER PRIMARY KEY CHECK(id=1), secret BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS pending_admin (email TEXT PRIMARY KEY, actor TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS beta_invitation (id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
    sub TEXT, kind TEXT NOT NULL CHECK(kind IN ('minutes','days','unlimited')), amount INTEGER NOT NULL,
    created REAL NOT NULL, expires REAL NOT NULL, activated REAL, revoked REAL);
CREATE INDEX IF NOT EXISTS beta_invitation_pending ON beta_invitation(email, sub);
CREATE TABLE IF NOT EXISTS google_identity (sub TEXT PRIMARY KEY, email TEXT NOT NULL, seen REAL NOT NULL);
CREATE INDEX IF NOT EXISTS google_identity_email ON google_identity(email);
CREATE TABLE IF NOT EXISTS account_activity (sub TEXT PRIMARY KEY, seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS report_assignment (report_id INTEGER PRIMARY KEY, sub TEXT NOT NULL);
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
        self.touch(db, sub)
        return token

    def bootstrap_roles(self, subjects):
        if not subjects:
            return
        with self.transaction() as db:
            if db.execute("INSERT OR IGNORE INTO staff_bootstrap VALUES(1)").rowcount:
                db.executemany("INSERT INTO staff VALUES(?,?)",
                               ((sub, "Owner" if index == 0 else "Admin")
                                for index, sub in enumerate(subjects)))

    @staticmethod
    def role(db, sub):
        row = db.execute("SELECT role FROM staff WHERE sub=?", (sub,)).fetchone()
        return row["role"] if row else "User"

    def touch(self, db, sub):
        db.execute("INSERT OR REPLACE INTO account_activity VALUES(?,?)", (sub, self.now()))

    @staticmethod
    def by_email(db, email):
        # Older signed Gmail profiles predate google_identity. Non-Gmail accounts
        # need a fresh sign-in proving that Google is authoritative for the address.
        rows = db.execute(
            """SELECT sub FROM google_identity WHERE email=?
               UNION SELECT sub FROM profile WHERE lower(email)=? AND
               (? LIKE '%@gmail.com' OR ? LIKE '%@googlemail.com') AND
               sub NOT IN (SELECT sub FROM google_identity) LIMIT 2""",
            (email, email, email, email),
        ).fetchall()
        if len(rows) > 1:
            raise Denied("Multiple Google accounts match this address")
        return rows[0]["sub"] if rows else None

    @staticmethod
    def revoke_sessions(db, sub):
        db.execute("DELETE FROM session WHERE sub=?", (sub,))
        db.execute("DELETE FROM lease WHERE sub=?", (sub,))
        db.execute("DELETE FROM oauth WHERE sub=?", (sub,))

    def observe_google(self, db, claims, state):
        sub = claims["sub"]
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str):
            db.execute("DELETE FROM profile WHERE sub=?", (sub,))
            db.execute("DELETE FROM google_identity WHERE sub=?", (sub,))
            return
        email = email.strip().lower()
        domain = email.rpartition("@")[2]
        db.execute("INSERT OR REPLACE INTO profile VALUES(?,?)", (sub, email))
        if not domain or (domain not in {"gmail.com", "googlemail.com"} and claims.get("hd") != domain):
            db.execute("DELETE FROM google_identity WHERE sub=?", (sub,))
            return
        db.execute("INSERT OR REPLACE INTO google_identity VALUES(?,?,?)", (sub, email, self.now()))
        try:
            verified = self.by_email(db, email)
        except Denied:
            return
        if verified != sub:
            return
        db.execute("UPDATE beta_invitation SET sub=? WHERE email=? AND sub IS NULL "
                   "AND activated IS NULL AND revoked IS NULL AND expires>?", (sub, email, self.now()))
        pending = db.execute("SELECT actor FROM pending_admin WHERE email=? AND created>?",
                             (email, self.now() - ADMIN_INVITATION_SECONDS)).fetchone()
        if pending and self.role(db, pending["actor"]) == "Owner":
            if db.execute("INSERT OR IGNORE INTO staff VALUES(?,'Admin')", (sub,)).rowcount:
                # Keep the verified OAuth claim currently completing. Old bearer sessions lose access.
                db.execute("DELETE FROM session WHERE sub=?", (sub,))
                db.execute("DELETE FROM lease WHERE sub=?", (sub,))
                db.execute("DELETE FROM oauth WHERE sub=? AND state<>?", (sub, state))
                self.audit(db, pending["actor"], sub, "Activated administrator")
            db.execute("DELETE FROM pending_admin WHERE email=?", (email,))

    def authenticate(self, db, token):
        row = db.execute("SELECT sub FROM session WHERE token=? AND until>?",
                         (digest(token), self.now())).fetchone()
        if row is None:
            raise Denied("Session expired")
        return row["sub"]

    def redeem(self, db, sub, token):
        modern = db.execute("SELECT * FROM beta_invitation WHERE code=?", (digest(token),)).fetchone()
        if modern:
            return self.redeem_email_invite(db, sub, modern)
        row = db.execute(
            "SELECT sub,used,until FROM invite LEFT JOIN invite_expiry USING(code) WHERE code=?",
            (digest(token),),
        ).fetchone()
        account = db.execute("SELECT invited, state FROM account WHERE sub=?", (sub,)).fetchone()
        if (not row or row["used"] or row["sub"] not in (sub, None)
                or (row["until"] is not None and row["until"] <= self.now())
                or account["invited"] or account["state"] in {"ACTIVE", "SUSPENDED"}):
            raise Denied("Invite unavailable")
        db.execute("UPDATE invite SET used=1 WHERE code=?", (digest(token),))
        db.execute("UPDATE account SET invited=1, state='BETA', remaining=remaining+?, until=NULL WHERE sub=?",
                   (BETA_SECONDS, sub))
        self.audit(db, sub, sub, "redeemed beta invite")

    def redeem_email_invite(self, db, sub, invite):
        account = db.execute("SELECT invited,state FROM account WHERE sub=?", (sub,)).fetchone()
        now = self.now()
        if (invite["sub"] != sub or invite["activated"] is not None or invite["revoked"] is not None
                or invite["expires"] <= now or account["invited"] or account["state"] in {"ACTIVE", "SUSPENDED"}):
            raise Denied("Invite unavailable")
        db.execute("UPDATE beta_invitation SET activated=? WHERE id=?", (now, invite["id"]))
        if invite["kind"] == "minutes":
            db.execute("UPDATE account SET invited=1,state='BETA',until=NULL,remaining=remaining+? WHERE sub=?",
                       (invite["amount"] * 60, sub))
        else:
            until = now + invite["amount"] * 86400 if invite["kind"] == "days" else None
            db.execute("UPDATE account SET invited=1,state='ACTIVE',until=? WHERE sub=?", (until, sub))
        self.audit(db, sub, sub, "Activated beta invitation")

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
            self.touch(db, sub)
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
