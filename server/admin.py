"""Email-based operations for the closed beta administration console."""
import hashlib
import hmac
import re
import secrets
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from calendar import monthrange
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from server.store import ADMIN_INVITATION_SECONDS, SESSION_SECONDS, Denied, digest


STATUSES = ("Received", "Reviewing", "Confirmed", "Fixed", "Closed")
AUDIT_PERIODS = {"5m": 300, "10m": 600, "30m": 1800, "1h": 3600,
                 "2h": 7200, "3h": 10800, "5h": 18000, "10h": 36000,
                 "1d": 86400, "1w": 604800, "2w": 1209600}


def audit_cutoff(now, period):
    if period in AUDIT_PERIODS:
        return now - AUDIT_PERIODS[period]
    current = datetime.fromtimestamp(now, timezone.utc)
    if period == "1mo":
        year = current.year - (current.month == 1)
        month = 12 if current.month == 1 else current.month - 1
    else:
        year, month = current.year - 1, current.month
    return current.replace(year=year, month=month,
                           day=min(current.day, monthrange(year, month)[1])).timestamp()


class Email(BaseModel):
    email: str = Field(max_length=254)


class Filters(BaseModel):
    search: str = Field(default="", max_length=254)
    status: str = Field(default="", max_length=20)
    report_email: str = Field(default="", max_length=254)
    page: int = Field(default=1, ge=1, le=1_000_000)
    sort: Literal["name", "email", "requests"] = "email"
    descending: bool = False


class StaffAccount(BaseModel):
    subject: str = Field(min_length=1, max_length=128)


class StaffRole(StaffAccount):
    email: str = Field(max_length=254)
    role: Literal["Owner", "Admin"]
    confirmed: Literal[True]


class Invitation(Email):
    kind: Literal["minutes", "days", "unlimited"]
    amount: int | None = Field(default=None, ge=0, le=10080)
    validity: int = Field(default=7, ge=1, le=30)
    operation_id: UUID

    @model_validator(mode="after")
    def validate_duration(self):
        if self.kind == "unlimited" and self.amount in (None, 0):
            self.amount = 0
        elif not (self.amount and (self.kind == "minutes" or self.kind == "days" and self.amount <= 365)):
            raise ValueError("Недійсна тривалість доступу")
        elif self.kind == "unlimited":
            raise ValueError("Безстроковий доступ не має тривалості")
        return self


class License(Email):
    subject: str = Field(default="", max_length=255)
    action: str = Field(pattern="^(minutes|remove|days|unlimited|suspend|restore|revoke)$")
    amount: int = Field(default=0, ge=0, le=10080)
    operation_id: UUID


class ConfirmedAction(BaseModel):
    confirmed: Literal[True]


class AuditClear(ConfirmedAction):
    period: Literal["5m", "10m", "30m", "1h", "2h", "3h", "5h", "10h",
                    "1d", "1w", "2w", "1mo", "1y"]


class ReportUpdate(BaseModel):
    status: str
    assignee: str = Field(default="", max_length=254)
    bonus: int = Field(default=0, ge=0, le=365)
    operation_id: UUID


class Selection(BaseModel):
    subjects: list[str] = Field(min_length=1, max_length=50)


class AccountMaintenance(Selection, ConfirmedAction):
    action: Literal["delete", "disable", "enable"]


class DeviceMaintenance(ConfirmedAction):
    action: Literal["unbind", "stale", "reset"]
    session: str = Field(default="", max_length=64)


VISIBLE_AUDIT = """NOT EXISTS (SELECT 1 FROM audit_hidden h WHERE h.id=l.id)
    AND l.action NOT LIKE 'Cleared audit %'
    AND l.action NOT LIKE 'Purged example.test QA data%'"""


def identity_status(row):
    if row["verified_email"]:
        return "confirmed"
    if row["email"]:
        return "legacy_email"
    return "missing_email" if row["sub"].isascii() and row["sub"].isdigit() else "legacy_record"


def erase_account(store, tx, sub):
    tx.execute("DELETE FROM report_attachment WHERE report_id IN (SELECT id FROM report WHERE sub=?)", (sub,))
    tx.execute("DELETE FROM report_assignment WHERE report_id IN (SELECT id FROM report WHERE sub=?) OR sub=?",
               (sub, sub))
    tx.execute("DELETE FROM invite_expiry WHERE code IN (SELECT code FROM invite WHERE sub=?)", (sub,))
    tx.execute("DELETE FROM invite WHERE sub=?", (sub,))
    email = tx.execute("SELECT email FROM google_identity WHERE sub=?", (sub,)).fetchone()
    if email:
        tx.execute("DELETE FROM pending_admin WHERE email=?", (email["email"],))
        tx.execute("DELETE FROM beta_invitation WHERE email=? AND sub IS NULL", (email["email"],))
    for table in ("beta_invitation", "report", "usage", "suspended_access", "staff",
                  "google_identity", "account_activity", "display_name", "profile", "account"):
        tx.execute(f"DELETE FROM {table} WHERE sub=?", (sub,))
    store.revoke_sessions(tx, sub)


def backup_for_deletion(store):
    path = Path(store.path)
    directory = path.resolve().parent / "deletion-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="before-deletion-", suffix=".sqlite", dir=directory)
    os.close(fd)
    try:
        with closing(sqlite3.connect(path)) as original, closing(sqlite3.connect(name)) as backup:
            original.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Deletion backup integrity check failed")
    except (sqlite3.Error, OSError):
        Path(name).unlink(missing_ok=True)
        raise


def normalize_email(value):
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", email):
        raise HTTPException(422, "Enter a valid Google email address")
    return email


def lookup(store, tx, email, *, required=False):
    try:
        sub = store.by_email(tx, email)
    except Denied as exc:
        raise HTTPException(409, str(exc)) from None
    if required and not sub:
        raise HTTPException(404, "No verified Google account for this email")
    return sub


def guard_target(store, tx, actor, target):
    if store.role(tx, actor) != "Owner" and store.role(tx, target) == "Owner":
        raise HTTPException(403, "Only the owner can change the owner's access")


def record_operation(tx, actor, operation_id, payload):
    previous = tx.execute("SELECT actor,payload FROM admin_operation WHERE id=?", (str(operation_id),)).fetchone()
    if previous:
        if (previous["actor"], previous["payload"]) != (actor, payload):
            raise HTTPException(409, "Operation ID already used")
        return False
    tx.execute("INSERT INTO admin_operation VALUES(?,?,?)", (str(operation_id), actor, payload))
    return True


def change_license(store, tx, sub, action, amount):
    account = tx.execute("SELECT * FROM account WHERE sub=?", (sub,)).fetchone()
    if not account:
        raise HTTPException(404, "Account not found")
    store.access(tx, sub)
    if action in {"minutes", "days", "remove"}:
        if not amount:
            raise HTTPException(422, "Amount must be positive")
        if account["state"] == "SUSPENDED":
            raise HTTPException(409, "Restore access before changing time")
    now = store.now()
    if action == "minutes":
        active = account["state"] == "ACTIVE" and (account["until"] is None or account["until"] > now)
        tx.execute("UPDATE account SET remaining=remaining+?,state=?,until=? WHERE sub=?",
                   (amount * 60, "ACTIVE" if active else "BETA", account["until"] if active else None, sub))
    elif action == "remove":
        tx.execute("UPDATE account SET remaining=max(0,remaining-?) WHERE sub=?", (amount * 60, sub))
    elif action == "days":
        if not (account["state"] == "ACTIVE" and account["until"] is None):
            until = max(now, account["until"] or 0) + amount * 86400
            tx.execute("UPDATE account SET state='ACTIVE',until=? WHERE sub=?", (until, sub))
    elif action == "unlimited" and account["state"] != "SUSPENDED":
        tx.execute("UPDATE account SET state='ACTIVE',until=NULL WHERE sub=?", (sub,))
    elif action == "suspend" and account["state"] != "SUSPENDED":
        tx.execute("INSERT INTO suspended_access VALUES(?,?)", (sub, account["state"]))
        tx.execute("UPDATE account SET state='SUSPENDED' WHERE sub=?", (sub,))
        tx.execute("DELETE FROM lease WHERE sub=?", (sub,))
    elif action == "restore" and account["state"] == "SUSPENDED":
        original = tx.execute("SELECT state FROM suspended_access WHERE sub=?", (sub,)).fetchone()
        tx.execute("UPDATE account SET state=? WHERE sub=?", (original["state"] if original else "EXPIRED", sub))
        tx.execute("DELETE FROM suspended_access WHERE sub=?", (sub,))
    elif action == "revoke":
        tx.execute("UPDATE account SET state=CASE WHEN state='SUSPENDED' THEN state ELSE 'EXPIRED' END,"
                   "remaining=0,until=? WHERE sub=?", (now, sub))
        tx.execute("UPDATE suspended_access SET state='EXPIRED' WHERE sub=?", (sub,))
        tx.execute("DELETE FROM lease WHERE sub=?", (sub,))
    else:
        raise HTTPException(409, "Account is already in the requested state")


def invitation_status(row, now):
    if row["activated"] is not None:
        return "Activated"
    if row["revoked"] is not None:
        return "Revoked"
    return "Expired" if row["expires"] <= now else "Pending"


def invitation_code(tx, invite_id):
    row = tx.execute("SELECT secret FROM invite_signing WHERE id=1").fetchone()
    if row:
        secret = row["secret"]
    else:
        secret = secrets.token_bytes(32)
        tx.execute("INSERT INTO invite_signing VALUES(1,?)", (secret,))
    return hmac.new(secret, ("examai invite v2 " + invite_id).encode(), hashlib.sha256).hexdigest()


def mount_admin(app, store, bearer, admin_subject):
    def owner(tx, token):
        actor = admin_subject(tx, token)
        if store.role(tx, actor) != "Owner":
            raise HTTPException(403, "Owner required")
        return actor

    @app.get("/admin/v2/qa/preview")
    def preview_qa(token=Depends(bearer)):
        with store.transaction() as tx:
            owner(tx, token)
            accounts = tx.execute(
                "SELECT count(*) FROM (SELECT sub FROM profile WHERE lower(email) LIKE '%@example.test' "
                "UNION SELECT sub FROM google_identity WHERE lower(email) LIKE '%@example.test')"
            ).fetchone()[0]
            invitations = tx.execute(
                "SELECT count(*) FROM beta_invitation WHERE lower(email) LIKE '%@example.test'"
            ).fetchone()[0]
            return {"accounts": accounts, "invitations": invitations}

    @app.post("/admin/v2/qa/purge")
    def purge_qa(body: ConfirmedAction, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            subs = {row["sub"] for row in tx.execute(
                "SELECT sub FROM profile WHERE lower(email) LIKE '%@example.test' "
                "UNION SELECT sub FROM google_identity WHERE lower(email) LIKE '%@example.test'"
            )}
            if any(store.role(tx, sub) == "Owner" for sub in subs):
                raise HTTPException(409, "A test account has owner access")
            invitations = 0
            if subs or tx.execute("SELECT 1 FROM beta_invitation WHERE lower(email) LIKE '%@example.test'").fetchone():
                backup_for_deletion(store)
            for sub in subs:
                store.revoke_sessions(tx, sub)
                tx.execute("DELETE FROM report_attachment WHERE report_id IN "
                           "(SELECT id FROM report WHERE sub=?)", (sub,))
                tx.execute("DELETE FROM report_assignment WHERE report_id IN "
                           "(SELECT id FROM report WHERE sub=?) OR sub=?", (sub, sub))
                invitations += tx.execute("DELETE FROM beta_invitation WHERE sub=?", (sub,)).rowcount
                for table in ("report", "usage", "session", "lease", "oauth", "invite",
                              "suspended_access", "staff", "google_identity", "account_activity",
                              "display_name", "profile", "account"):
                    tx.execute(f"DELETE FROM {table} WHERE sub=?", (sub,))
                tx.execute("DELETE FROM admin_operation WHERE actor=?", (sub,))
            tx.execute("DELETE FROM admin_operation WHERE lower(payload) LIKE '%@example.test%'")
            invitations += tx.execute("DELETE FROM beta_invitation WHERE lower(email) LIKE '%@example.test'").rowcount
            tx.execute("DELETE FROM pending_admin WHERE lower(email) LIKE '%@example.test'")
            store.audit(tx, actor, actor, f"Purged example.test QA data: {len(subs)} accounts")
            return {"accounts": len(subs), "invitations": invitations}

    @app.post("/admin/v2/audit/clear")
    def clear_audit(body: AuditClear, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            now = store.now()
            count = tx.execute(
                f"""INSERT INTO audit_hidden(id) SELECT l.id FROM audit l
                    WHERE l.created BETWEEN ? AND ? AND {VISIBLE_AUDIT}""",
                (audit_cutoff(now, body.period), now),
            ).rowcount
            store.audit(tx, actor, actor, f"Cleared audit {body.period}: {count} entries")
            return {"deleted": count}

    @app.get("/admin/v2/audit/preview")
    def preview_audit(period: Literal["5m", "10m", "30m", "1h", "2h", "3h", "5h", "10h",
                                      "1d", "1w", "2w", "1mo", "1y"], token=Depends(bearer)):
        with store.transaction() as tx:
            owner(tx, token)
            now = store.now()
            count = tx.execute(
                f"SELECT count(*) FROM audit l WHERE l.created BETWEEN ? AND ? AND {VISIBLE_AUDIT}",
                (audit_cutoff(now, period), now),
            ).fetchone()[0]
            return {"count": count}

    @app.post("/admin/v2/overview")
    def overview(filters: Filters, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = admin_subject(tx, token)
            now = store.now()
            day = int(now // 86400)
            tx.create_function("casefold", 1, str.casefold, deterministic=True)
            search = filters.search.strip().casefold()
            joins = """FROM account a
                       LEFT JOIN profile p USING(sub)
                       LEFT JOIN display_name n USING(sub)
                       LEFT JOIN google_identity g USING(sub)
                       LEFT JOIN disabled_account d USING(sub)"""
            matches = """WHERE instr(casefold(coalesce(p.email,'')),?)>0
                            OR instr(casefold(coalesce(n.name,'')),?)>0"""
            count = tx.execute(f"SELECT count(*) {joins} {matches}", (search, search)).fetchone()[0]
            page_size = 25
            pages = max(1, (count + page_size - 1) // page_size)
            page = min(pages, filters.page)
            sort = {"name": "casefold(coalesce(n.name,''))", "email": "casefold(coalesce(p.email,''))",
                    "requests": "requests"}[filters.sort]
            direction = "DESC" if filters.descending else "ASC"
            users = []
            for row in tx.execute(
                f"""SELECT a.sub,p.email,n.name,g.email AS verified_email,d.sub IS NOT NULL AS disabled,s.role,x.seen,coalesce(u.requests,0) AS requests
                    {joins} LEFT JOIN staff s USING(sub)
                    LEFT JOIN account_activity x USING(sub) LEFT JOIN usage u ON u.sub=a.sub AND u.day=?
                    {matches} ORDER BY {sort} {direction}, a.sub LIMIT ? OFFSET ?""",
                (day, search, search, page_size, (page - 1) * page_size),
            ).fetchall():
                account = store.access(tx, row["sub"])
                license_state = tx.execute("SELECT state FROM suspended_access WHERE sub=?", (row["sub"],)).fetchone()
                state = license_state["state"] if license_state else account["state"]
                license_type = ("Unlimited" if account["until"] is None else "Calendar")
                if state != "ACTIVE":
                    license_type = "Minutes" if account["seconds"] else "None"
                users.append(dict(subject=row["sub"], email=row["email"], name=row["name"], role=row["role"] or "User", last_active=row["seen"],
                                  requests=row["requests"], license=license_type, identity=identity_status(row),
                                  disabled=bool(row["disabled"]), **account))
            invites = [dict(row, status=invitation_status(row, now)) for row in tx.execute(
                "SELECT id,email,kind,amount,created,expires,activated,revoked FROM beta_invitation "
                "ORDER BY created DESC LIMIT 100")]
            staff = [dict(row) for row in tx.execute(
                "SELECT staff.sub,role,email FROM staff LEFT JOIN profile USING(sub) ORDER BY role DESC,email")]
            staff += [dict(email=row["email"], role="Pending" if row["created"] > now - ADMIN_INVITATION_SECONDS
                           else "Expired") for row in tx.execute("SELECT email,created FROM pending_admin ORDER BY email")]
            reports = [dict(row) for row in tx.execute(
                """SELECT r.id,r.kind,r.description,r.status,r.created,p.email,assignee.email AS assignee,
                          (SELECT group_concat(kind) FROM report_attachment WHERE report_id=r.id) AS attachments
                   FROM report r LEFT JOIN profile p ON p.sub=r.sub
                   LEFT JOIN report_assignment a ON a.report_id=r.id
                   LEFT JOIN profile assignee ON assignee.sub=a.sub
                   WHERE (?='' OR r.status=?) AND (?='' OR lower(p.email)=?)
                   ORDER BY r.created DESC LIMIT 100""",
                (filters.status, filters.status, filters.report_email, filters.report_email))]
            audit = [dict(row) for row in tx.execute(
                f"""SELECT l.action,l.created,p.email AS actor,t.email AS target,
                          CASE WHEN instr(l.subject,'@')>0 THEN l.subject END AS pending_email
                   FROM audit l LEFT JOIN profile p ON p.sub=l.actor
                   LEFT JOIN profile t ON t.sub=l.subject WHERE {VISIBLE_AUDIT} ORDER BY l.id DESC LIMIT 100""")]
            summary = dict(tx.execute(
                """SELECT (SELECT count(*) FROM account) AS users,
                          (SELECT count(*) FROM beta_invitation WHERE activated IS NULL AND revoked IS NULL
                             AND expires>?) AS pending,
                          (SELECT coalesce(sum(requests),0) FROM usage WHERE day=?) AS requests,
                          (SELECT count(*) FROM report WHERE status NOT IN ('Fixed','Closed')) AS reports""",
                (now, day)).fetchone())
            return dict(role=store.role(tx, actor), summary=summary, users=users, invitations=invites,
                        staff=staff, reports=reports, audit=audit,
                        user_pagination=dict(page=page, pages=pages, total=count))

    def selection(tx, subjects):
        rows = []
        for sub in dict.fromkeys(subjects):
            row = tx.execute(
                """SELECT a.sub,p.email,n.name,s.role
                     FROM account a LEFT JOIN profile p USING(sub)
                     LEFT JOIN display_name n USING(sub) LEFT JOIN staff s USING(sub)
                     WHERE a.sub=?""", (sub,)).fetchone()
            if not row:
                raise HTTPException(404, "Account not found")
            if row["role"] == "Owner":
                raise HTTPException(403, "Owner accounts are protected")
            counts = dict(tx.execute(
                """SELECT (SELECT count(*) FROM report WHERE sub=?) AS reports,
                          (SELECT count(*) FROM session WHERE sub=? AND until>?) AS sessions,
                          ((SELECT count(*) FROM beta_invitation WHERE sub=? OR
                            (email=(SELECT email FROM google_identity WHERE sub=?) AND sub IS NULL)) +
                           (SELECT count(*) FROM invite WHERE sub=?)) AS invitations""",
                (sub, sub, store.now(), sub, sub, sub)).fetchone())
            rows.append(dict(row, **counts))
        return rows

    @app.post("/admin/v2/users/balances")
    def balances(body: Selection, token=Depends(bearer)):
        with store.transaction() as tx:
            admin_subject(tx, token)
            return {sub: store.access(tx, sub) for sub in dict.fromkeys(body.subjects)
                    if tx.execute("SELECT 1 FROM account WHERE sub=?", (sub,)).fetchone()}

    @app.post("/admin/v2/users/preview")
    def preview_accounts(body: Selection, token=Depends(bearer)):
        with store.transaction() as tx:
            owner(tx, token)
            return {"accounts": selection(tx, body.subjects)}

    @app.post("/admin/v2/users/maintenance")
    def maintain_accounts(body: AccountMaintenance, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            rows = selection(tx, body.subjects)
            if body.action == "delete":
                try:
                    backup_for_deletion(store)
                except (sqlite3.Error, OSError) as exc:
                    raise HTTPException(503, "Could not back up accounts") from exc
            for row in rows:
                sub = row["sub"]
                if body.action == "enable":
                    tx.execute("DELETE FROM disabled_account WHERE sub=?", (sub,))
                else:
                    tx.execute("INSERT OR IGNORE INTO disabled_account VALUES(?)", (sub,))
                    if body.action == "delete":
                        erase_account(store, tx, sub)
                    else:
                        sessions = tx.execute("SELECT token FROM session WHERE sub=?", (sub,)).fetchall()
                        for session in sessions:
                            store.end_device(tx, sub, session["token"])
                        store.revoke_sessions(tx, sub)
                store.audit(tx, actor, sub, f"Account maintenance: {body.action}")
            return {"count": len(rows)}

    def devices(tx, sub):
        if not tx.execute("SELECT 1 FROM account WHERE sub=?", (sub,)).fetchone():
            raise HTTPException(404, "Account not found")
        return tx.execute(
            """SELECT s.token, coalesce(d.seen,s.until-?) AS seen,
                      (l.token IS NOT NULL) AS active FROM session s
                      LEFT JOIN desktop_seen d USING(token)
                      LEFT JOIN lease l ON l.token=s.token AND l.until>?
                      WHERE s.sub=? AND s.until>? ORDER BY seen DESC""",
            (SESSION_SECONDS, store.now(), sub, store.now())).fetchall()

    @app.get("/admin/v2/devices/{sub}")
    def list_devices(sub: str, token=Depends(bearer)):
        with store.transaction() as tx:
            owner(tx, token)
            return {"devices": [dict(id=digest(row["token"]), seen=row["seen"], active=bool(row["active"]))
                                for row in devices(tx, sub)]}

    @app.post("/admin/v2/devices/{sub}")
    def maintain_devices(sub: str, body: DeviceMaintenance, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            rows = devices(tx, sub)
            if body.action == "unbind":
                selected = [row for row in rows if digest(row["token"]) == body.session]
                if not selected:
                    raise HTTPException(404, "Device no longer registered")
            elif body.action == "stale":
                selected = [row for row in rows if not row["active"] and row["seen"] < store.now() - 600]
            else:
                selected = rows
            for row in selected:
                store.end_device(tx, sub, row["token"])
            store.audit(tx, actor, sub, f"Device maintenance: {body.action} ({len(selected)})")
            return {"count": len(selected)}

    @app.post("/admin/v2/staff")
    def add_admin(body: Email, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            email = normalize_email(body.email)
            sub = lookup(store, tx, email)
            if sub:
                if tx.execute("INSERT OR IGNORE INTO staff VALUES(?,'Admin')", (sub,)).rowcount:
                    store.revoke_sessions(tx, sub)
                    store.audit(tx, actor, sub, "Appointed administrator")
                tx.execute("DELETE FROM pending_admin WHERE email=?", (email,))
                return {"role": store.role(tx, sub)}
            tx.execute("DELETE FROM pending_admin WHERE email=? AND created<=?",
                       (email, store.now() - ADMIN_INVITATION_SECONDS))
            if tx.execute("INSERT OR IGNORE INTO pending_admin VALUES(?,?,?)",
                          (email, actor, store.now())).rowcount:
                store.audit(tx, actor, email, "Invited administrator")
            return {"role": "Pending"}

    @app.patch("/admin/v2/staff")
    def change_staff_role(body: StaffRole, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            email = normalize_email(body.email)
            current = store.role(tx, body.subject)
            if current == "User" or lookup(store, tx, email) != body.subject:
                raise HTTPException(409, "Підтверджений обліковий запис змінився. Оновіть список.")
            if body.role != current:
                if current == "Owner" and tx.execute("SELECT count(*) FROM staff WHERE role='Owner'").fetchone()[0] < 2:
                    raise HTTPException(409, "Останнього власника не можна понизити.")
                tx.execute("UPDATE staff SET role=? WHERE sub=?", (body.role, body.subject))
                store.revoke_sessions(tx, body.subject)
                store.audit(tx, actor, body.subject, f"Role {current} → {body.role}")
            return {"role": body.role}

    @app.delete("/admin/v2/staff")
    def remove_admin(body: StaffAccount, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            sub = body.subject
            if store.role(tx, sub) == "Owner":
                raise HTTPException(409, "The owner cannot be removed")
            if not tx.execute("DELETE FROM staff WHERE sub=? AND role='Admin'", (sub,)).rowcount:
                raise HTTPException(404, "Administrator not found")
            store.revoke_sessions(tx, sub)
            store.audit(tx, actor, sub, "Revoked administrator")
            return {"ok": True}

    @app.delete("/admin/v2/pending-staff")
    def cancel_admin(body: Email, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = owner(tx, token)
            email = normalize_email(body.email)
            if tx.execute("DELETE FROM pending_admin WHERE email=?", (email,)).rowcount:
                store.audit(tx, actor, email, "Cancelled administrator invitation")
            return {"ok": True}

    @app.post("/admin/v2/invitations")
    def make_invitation(body: Invitation, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = admin_subject(tx, token)
            email = normalize_email(body.email)
            sub = lookup(store, tx, email)
            if sub:
                guard_target(store, tx, actor, sub)
            invite_id = str(body.operation_id)
            code = invitation_code(tx, invite_id)
            if record_operation(tx, actor, body.operation_id, "invite:" + body.model_dump_json()):
                now = store.now()
                tx.execute("INSERT INTO beta_invitation VALUES(?,?,?,?,?,?,?,?,NULL,NULL)",
                           (invite_id, digest(code), email, sub, body.kind, body.amount, now,
                            now + body.validity * 86400))
                store.audit(tx, actor, sub or email, f"Created {body.kind} invitation")
            return {"id": invite_id, "code": code}

    @app.get("/admin/v2/invitations/{invite_id}/code")
    def copy_invitation(invite_id: UUID, token=Depends(bearer)):
        with store.transaction() as tx:
            admin_subject(tx, token)
            row = tx.execute("SELECT * FROM beta_invitation WHERE id=?", (str(invite_id),)).fetchone()
            if not row or invitation_status(row, store.now()) != "Pending":
                raise HTTPException(409, "Only pending invitations can be copied")
            code = invitation_code(tx, str(invite_id))
            if not hmac.compare_digest(digest(code), row["code"]):
                raise HTTPException(409, "This code was created before the current signing key")
            return {"code": code}

    @app.delete("/admin/v2/invitations/{invite_id}")
    def revoke_invitation(invite_id: UUID, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = admin_subject(tx, token)
            row = tx.execute("SELECT * FROM beta_invitation WHERE id=?", (str(invite_id),)).fetchone()
            if not row or invitation_status(row, store.now()) != "Pending":
                raise HTTPException(409, "Only pending invitations can be revoked")
            guard_target(store, tx, actor, row["sub"])
            tx.execute("UPDATE beta_invitation SET revoked=? WHERE id=?", (store.now(), str(invite_id)))
            store.audit(tx, actor, row["sub"] or row["email"], "Revoked beta invitation")
            return {"ok": True}

    @app.post("/admin/v2/licenses")
    def update_license(body: License, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = admin_subject(tx, token)
            sub = lookup(store, tx, normalize_email(body.email), required=True)
            if body.subject and body.subject != sub:
                raise HTTPException(409, "Google account changed. Refresh user list")
            guard_target(store, tx, actor, sub)
            if body.action == "unlimited" and store.role(tx, actor) != "Owner":
                raise HTTPException(403, "Owner required")
            if (body.action == "days" and body.amount > 365):
                raise HTTPException(422, "Maximum extension is 365 days")
            if record_operation(tx, actor, body.operation_id, "license:" + body.model_dump_json(exclude={"subject"} if not body.subject else None)):
                change_license(store, tx, sub, body.action, body.amount)
                store.audit(tx, actor, sub, f"License {body.action} {body.amount}")
            return {"ok": True}

    @app.post("/admin/v2/reports/{report_id}")
    def update_report(report_id: int, body: ReportUpdate, token=Depends(bearer)):
        with store.transaction() as tx:
            actor = admin_subject(tx, token)
            report = tx.execute("SELECT sub,status FROM report WHERE id=?", (report_id,)).fetchone()
            if not report:
                raise HTTPException(404, "Report not found")
            guard_target(store, tx, actor, report["sub"])
            if body.status not in STATUSES:
                raise HTTPException(422, "Invalid report status")
            assignee = None
            if body.assignee:
                assignee = lookup(store, tx, normalize_email(body.assignee), required=True)
                if store.role(tx, assignee) == "User":
                    raise HTTPException(422, "Select an administrator")
            if record_operation(tx, actor, body.operation_id, f"report:{report_id}:" + body.model_dump_json()):
                tx.execute("UPDATE report SET status=? WHERE id=?", (body.status, report_id))
                tx.execute("DELETE FROM report_assignment WHERE report_id=?", (report_id,))
                if assignee:
                    tx.execute("INSERT INTO report_assignment VALUES(?,?)", (report_id, assignee))
                if body.bonus:
                    change_license(store, tx, report["sub"], "minutes", body.bonus)
                store.audit(tx, actor, report["sub"], f"Report #{report_id}: {report['status']} → {body.status}; "
                            f"assignee {body.assignee or 'none'}; bonus {body.bonus} min")
            return {"ok": True}

    @app.get("/admin/v2/reports/{report_id}/history")
    def report_history(report_id: int, token=Depends(bearer)):
        with store.transaction() as tx:
            admin_subject(tx, token)
            report = tx.execute("SELECT sub FROM report WHERE id=?", (report_id,)).fetchone()
            if not report:
                raise HTTPException(404, "Report not found")
            return [dict(row) for row in tx.execute(
                """SELECT l.action,l.created,p.email AS actor FROM audit l LEFT JOIN profile p ON p.sub=l.actor
                   WHERE l.subject=? AND l.action LIKE ? ORDER BY l.id DESC LIMIT 100""",
                (report["sub"], f"Report #{report_id}:%"))]
