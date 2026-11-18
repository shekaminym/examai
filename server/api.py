"""EXAM AI beta service. Run behind HTTPS with one persistent SQLite volume."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
from unicodedata import category
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from time import monotonic, perf_counter
from urllib.parse import urlencode, urlsplit
from threading import Event, Lock, Thread
from uuid import UUID
from typing import Literal

import httpx
from fastapi import Request as FastAPIRequest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token
from pydantic import BaseModel, Field

from examai.ai.llm_engine import GenerationError, LLMEngine, ProviderRateLimit, ProviderUnavailable
from examai.report_images import normalize_report_image
from server.store import ADMIN_SESSION_SECONDS
from server.admin import STATUSES, change_license, guard_target, mount_admin, record_operation
from server.cloud_budget import CloudBudget
from server import cloud_queue
from server.payments import mount as mount_payments
from server.store import Denied, Store, StoreBusy, digest


PLANS = tuple(dict(id=str(months), months=months, price_uah=price)
              for months, price in ((1, 149), (3, 399), (6, 699), (12, 1299)))
log = logging.getLogger("uvicorn.error")


class Device(BaseModel):
    device: str = Field(min_length=16, max_length=128)


class BrowserDevice(BaseModel):
    previous: str = Field(default="", pattern=r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})?$")


class Claim(BaseModel):
    claim: str = Field(min_length=32, max_length=128)
    replace: bool = False


class AccountName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class Invite(BaseModel):
    code: str = Field(min_length=16, max_length=128)


class Problem(BaseModel):
    kind: str = Field(pattern="^(Bug|Suggestion)$")
    description: str = Field(min_length=5, max_length=4000)
    screenshot: str = Field(default="", max_length=2_800_000)
    diagnostics: str = Field(default="", max_length=4096)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=3500)
    image: str = Field(default="", max_length=3_500_000)
    language: Literal["uk", "en"] = "uk"
    queue: bool = False
    mime: str = Field(default="image/png", pattern="^image/(png|jpeg)$")


class AdminAction(BaseModel):
    sub: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern="^(invite|minutes|days|suspend|restore|report)$")
    amount: int = Field(default=0, ge=0, le=365)
    report_id: int = 0
    status: str = ""
    invite_days: int = Field(default=7, ge=1, le=30)
    operation_id: UUID | None = None


def create_app(path=None):
    app = FastAPI(title="EXAM AI Closed Beta")
    db = Store(path or os.getenv("EXAMAI_SERVER_DB", "examai-server.db"))
    configured_staff = dict.fromkeys(filter(None, map(str.strip, os.getenv("EXAMAI_ADMIN_SUBJECTS", "").split(","))))
    db.bootstrap_roles(configured_staff)
    client_id = os.getenv("EXAMAI_GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("EXAMAI_GOOGLE_CLIENT_SECRET", "")
    base = os.getenv("EXAMAI_PUBLIC_URL", "").rstrip("/")
    budget = (CloudBudget() if os.getenv("EXAMAI_CLOUD_DAILY_BUDGET_MICROUSD")
              or (urlsplit(base).scheme == "https" and os.getenv("EXAMAI_CLOUD_GEMINI_KEY")) else None)
    redirect = base + "/oauth/callback"
    inflight = {}
    waiting = {}
    recent_submissions = {}
    inflight_lock = Lock()
    cloud_parallel = min(2, int(os.getenv("EXAMAI_CLOUD_MAX_PARALLEL", "2")))
    cloud_per_minute = int(os.getenv("EXAMAI_CLOUD_MAX_PER_MINUTE", "24"))
    if not (cloud_parallel > 0 and cloud_per_minute > 0):
        raise ValueError("Cloud concurrency and rate limits must be positive")

    @app.get("/cloud/capabilities")
    def cloud_capabilities():
        return {"answer_queue": True}

    def bearer(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 128:
            raise HTTPException(401, "Sign in to EXAM AI")
        return authorization[7:]

    def subject(tx, token):
        try:
            return db.authenticate(tx, token)
        except Denied as exc:
            raise HTTPException(401, str(exc)) from None

    cookie_name = "__Host-examai-admin"
    admin_device_prefix = "admin-cookie:"

    def csrf(request):
        if request.headers.get("origin") != base or request.headers.get("x-examai-request") != "admin":
            raise HTTPException(403, "Origin not allowed")

    def admin_credentials(request: FastAPIRequest, authorization: str = Header(default="")):
        if authorization:
            return "desktop", bearer(authorization)
        token = request.cookies.get(cookie_name, "")
        if not 32 <= len(token) <= 128:
            raise HTTPException(401, "Sign in to EXAM AI")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf(request)
        return "browser", token

    def admin_subject(tx, credentials):
        kind, token = credentials
        if kind == "browser":
            try:
                sub = db.authenticate_admin(tx, token)
            except Denied as exc:
                raise HTTPException(401, str(exc)) from None
        else:
            sub = subject(tx, token)
        if db.role(tx, sub) == "User":
            raise HTTPException(403, "Administrator required")
        return sub

    mount_admin(app, db, admin_credentials, admin_subject)

    @app.exception_handler(StoreBusy)
    async def database_busy(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(RequestValidationError)
    async def explain_invite_validation(request, exc):
        if request.url.path == "/invite":
            return JSONResponse({"detail": "Вставте повний код запрошення (від 16 до 128 символів)."}, status_code=422)
        if request.url.path == "/admin/v2/invitations":
            return JSONResponse({"detail": "Перевірте адресу, тривалість доступу та строк дії коду."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def prevent_admin_caching(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/admin", "/oauth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/oauth/start")
    def oauth_start(body: Device):
        if body.device.startswith(admin_device_prefix):
            raise HTTPException(422, "Invalid device")
        return start_flow(body.device)

    @app.post("/admin/session/start")
    def browser_oauth_start(body: BrowserDevice, request: FastAPIRequest):
        csrf(request)
        return start_flow(admin_device_prefix + body.previous)

    def start_flow(device):
        if not client_id or not client_secret or urlsplit(base).scheme != "https":
            raise HTTPException(503, "Google Sign-In is not configured on the server")
        state, verifier, nonce, claim = (secrets.token_urlsafe(32) for _ in range(4))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with db.transaction() as tx:
            tx.execute("DELETE FROM oauth WHERE until <= ?", (db.now(),))
            if tx.execute("SELECT count(*) FROM oauth").fetchone()[0] >= 256:
                raise HTTPException(429, "Too many sign-in attempts. Try again shortly.")
            tx.execute("INSERT INTO oauth VALUES (?,?,?,?,?,?,NULL)",
                       (state, verifier, nonce, digest(claim), device, db.now() + 300))
        query = urlencode(dict(client_id=client_id, redirect_uri=redirect, response_type="code",
                               scope="openid email", state=state, nonce=nonce, code_challenge=challenge,
                               code_challenge_method="S256", prompt="select_account"))
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + query, "claim": claim}

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(state: str, code: str = "", error: str = ""):
        if error and not code:
            with db.transaction() as tx:
                tx.execute("DELETE FROM oauth WHERE state=? AND verifier<>''", (state,))
            return HTMLResponse("EXAM AI: sign-in cancelled. Return to the application.", headers={
                "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            })
        if not code:
            raise HTTPException(400, "Missing authorization code")
        with db.transaction() as tx:
            row = tx.execute("SELECT * FROM oauth WHERE state=? AND until>? AND verifier<>''",
                             (state, db.now())).fetchone()
            if not row:
                raise HTTPException(400, "Login link expired")
            tx.execute("UPDATE oauth SET verifier='' WHERE state=?", (state,))
        try:
            response = httpx.post("https://oauth2.googleapis.com/token", timeout=12, data=dict(
                code=code, client_id=client_id, client_secret=client_secret, redirect_uri=redirect,
                grant_type="authorization_code", code_verifier=row["verifier"]))
            response.raise_for_status()
            claims = verify_oauth2_token(response.json()["id_token"],
                                         partial(Request(), timeout=12), client_id)
            if claims.get("nonce") != row["nonce"] or not claims.get("sub"):
                raise ValueError("Invalid OIDC response")
        except (TransportError, httpx.RequestError) as exc:
            log.warning("Google Sign-In network failure: %s", type(exc).__name__)
            raise HTTPException(503, "Google Sign-In is temporarily unavailable") from None
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(400, "Google authentication failed") from None
        with db.transaction() as tx:
            try:
                db.check_enabled(tx, claims["sub"])
            except Denied as exc:
                raise HTTPException(403, str(exc)) from None
            changed = tx.execute("UPDATE oauth SET sub=? WHERE state=? AND until>?",
                                 (claims["sub"], state, db.now())).rowcount
            if not changed:
                raise HTTPException(410, "Login expired")
            db.observe_google(tx, claims, state)
        return HTMLResponse("EXAM AI: sign-in confirmed. Return to the application.", headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        })

    def claim_row(tx, claim, *, browser):
        row = tx.execute("SELECT * FROM oauth WHERE claim=? AND until>?",
                         (digest(claim), db.now())).fetchone()
        if not row or row["device"].startswith(admin_device_prefix) != browser:
            raise HTTPException(410, "Login expired")
        return row

    @app.post("/oauth/claim")
    def oauth_claim(body: Claim):
        with db.transaction() as tx:
            row = claim_row(tx, body.claim, browser=False)
            if not row["sub"]:
                return {"pending": True}
            try:
                token = db.issue(tx, row["sub"], row["device"], replace=body.replace)
            except Denied as exc:
                raise HTTPException(409, str(exc)) from None
            tx.execute("DELETE FROM oauth WHERE state=?", (row["state"],))
            return {"token": token}

    @app.post("/admin/session/claim")
    def browser_oauth_claim(body: Claim, request: FastAPIRequest, response: Response):
        csrf(request)
        with db.transaction() as tx:
            row = claim_row(tx, body.claim, browser=True)
            if not row["sub"]:
                return {"pending": True}
            if db.role(tx, row["sub"]) == "User":
                raise HTTPException(403, "Administrator required")
            try:
                token = db.issue_admin(tx, row["sub"], request.cookies.get(cookie_name, ""))
                legacy_device = row["device"][len(admin_device_prefix):]
                if legacy_device:
                    old = tx.execute("SELECT token FROM session WHERE sub=? AND device=?",
                                     (row["sub"], legacy_device)).fetchone()
                    if old:
                        db.end_device(tx, row["sub"], old["token"])
                        db.audit(tx, row["sub"], row["sub"], "Migrated legacy browser session")
            except Denied as exc:
                raise HTTPException(403, str(exc)) from None
            tx.execute("DELETE FROM oauth WHERE state=?", (row["state"],))
        response.set_cookie(cookie_name, token, max_age=ADMIN_SESSION_SECONDS,
                            httponly=True, secure=True, samesite="strict", path="/")
        return {"ok": True}

    @app.get("/admin/session")
    def browser_session(credentials=Depends(admin_credentials)):
        with db.transaction() as tx:
            sub = admin_subject(tx, credentials)
            return {"role": db.role(tx, sub)}

    @app.post("/admin/session/logout")
    def browser_logout(request: FastAPIRequest, response: Response):
        csrf(request)
        token = request.cookies.get(cookie_name, "")
        if token:
            with db.transaction() as tx:
                tx.execute("DELETE FROM admin_session WHERE token=?", (digest(token),))
        response.delete_cookie(cookie_name, path="/", httponly=True, secure=True, samesite="strict")
        return {"ok": True}

    @app.post("/logout")
    def logout(token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            db.access(tx, sub, token=digest(token), lease_action="stop")
            tx.execute("DELETE FROM session WHERE token=?", (digest(token),))
        return {"ok": True}

    @app.get("/account")
    def account(token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            account = db.access(tx, sub, token=digest(token))
            profile = tx.execute("SELECT email FROM profile WHERE sub=?", (sub,)).fetchone()
            name = tx.execute("SELECT name FROM display_name WHERE sub=?", (sub,)).fetchone()
            account["email"] = profile["email"] if profile else None
            account["name"] = name["name"] if name else ""
            account["plan"] = {"BETA": "Closed Beta", "TRIAL": "Trial",
                               "ACTIVE": "Granted access"}.get(account["state"])
            account["auto_renew"] = False
            account["profile_id"] = digest(sub)
            account["role"] = db.role(tx, sub)
            return account

    @app.put("/account/name")
    def account_name(body: AccountName, token=Depends(bearer)):
        name = " ".join(body.name.split())
        if not name or len(name) > 60 or any(category(char) in {"Cc", "Cf", "Cs"} for char in body.name):
            raise HTTPException(422, "Enter a name of up to 60 characters")
        with db.transaction() as tx:
            sub = subject(tx, token)
            tx.execute("INSERT INTO display_name(sub,name) VALUES(?,?) "
                       "ON CONFLICT(sub) DO UPDATE SET name=excluded.name", (sub, name))
        return {"name": name}

    @app.post("/invite")
    def invite(body: Invite, token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            try:
                db.redeem(tx, sub, body.code.strip())
            except Denied as exc:
                raise HTTPException(409, "Запрошення недійсне, уже використане або призначене іншому обліковому запису.") from None
            return db.access(tx, sub, token=digest(token))

    @app.post("/activity/{action}")
    def activity(action: str, token=Depends(bearer)):
        if action not in {"start", "stop"}:
            raise HTTPException(404)
        with inflight_lock, db.transaction() as tx:
            sub = subject(tx, token)
            if action == "start" and inflight.get(sub):
                raise HTTPException(409, "Cloud generation is already metered by the server")
            return db.access(tx, sub, token=digest(token), lease_action=action)


    checkout_enabled = mount_payments(app, db, bearer, subject, PLANS, base,
                                      os.getenv("EXAMAI_LIQPAY_SANDBOX_PUBLIC", ""),
                                      os.getenv("EXAMAI_LIQPAY_SANDBOX_PRIVATE", ""))

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ready")
    def ready(response: Response):
        oauth = bool(client_id and client_secret and urlsplit(base).scheme == "https")
        cloud = bool(os.getenv("EXAMAI_CLOUD_GEMINI_KEY") and budget)
        response.status_code = 200 if oauth and cloud else 503
        return {"ready": oauth and cloud}

    @app.get("/plans")
    def plans():
        return {"plans": PLANS, "checkout_available": checkout_enabled}

    @app.post("/reports")
    def report(body: Problem, token=Depends(bearer)):
        with db.transaction() as tx:
            subject(tx, token)
        try:
            screenshot = base64.b64decode(body.screenshot, validate=True)
            if screenshot:
                screenshot = normalize_report_image(screenshot)
        except ValueError as exc:
            raise HTTPException(422, "Use a valid PNG, JPEG, WebP, BMP or GIF up to 2 MB and 12.5 megapixels") from exc
        with db.transaction() as tx:
            sub = subject(tx, token)
            count = tx.execute("SELECT count(*) FROM report WHERE sub=? AND created>?",
                               (sub, db.now() - 86400)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, "Daily report limit reached")
            if screenshot or body.diagnostics:
                used = tx.execute("SELECT coalesce(sum(length(content)),0) FROM report_attachment").fetchone()[0]
                if used + len(screenshot) + len(body.diagnostics.encode()) > 64 * 1024 * 1024:
                    raise HTTPException(413, "Attachment storage is full. Send the report without attachments.")
            row = tx.execute("INSERT INTO report(sub,kind,description,created) VALUES(?,?,?,?)",
                             (sub, body.kind, body.description, db.now()))
            for kind, content in (("screenshot", screenshot), ("diagnostics", body.diagnostics.encode())):
                if content:
                    tx.execute("INSERT INTO report_attachment VALUES(?,?,?)", (row.lastrowid, kind, content))
            return {"id": row.lastrowid, "status": "Received"}

    @app.get("/reports")
    def reports(token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            return [dict(row) for row in tx.execute(
                "SELECT id,kind,status,created FROM report WHERE sub=? ORDER BY id DESC LIMIT 100", (sub,))]

    @app.get("/admin/data")
    def admin_data(q: str = "", token=Depends(admin_credentials)):
        with db.transaction() as tx:
            admin_subject(tx, token)
            return {
                "users": [dict(row) for row in tx.execute(
                    "SELECT account.*,profile.email FROM account LEFT JOIN profile USING(sub) "
                    "WHERE instr(sub,?) > 0 OR instr(lower(coalesce(email,'')),lower(?)) > 0 "
                    "ORDER BY sub LIMIT 100", (q, q))],
                "reports": [dict(row) for row in tx.execute(
                    "SELECT report.*, (SELECT group_concat(kind) FROM report_attachment "
                    "WHERE report_id=report.id) AS attachments FROM report ORDER BY id DESC LIMIT 100")],
                "audit": [dict(row) for row in tx.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")],
                "usage": [dict(row) for row in tx.execute(
                    "SELECT day,sum(requests) AS requests,sum(units) AS units FROM usage "
                    "GROUP BY day ORDER BY day DESC LIMIT 30")],
                "cloud_cost": [dict(row) for row in tx.execute(
                    "SELECT day,sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,"
                    "sum(micro_usd) AS reserved_or_estimated_micro_usd FROM cloud_charge "
                    "GROUP BY day ORDER BY day DESC LIMIT 30")],
            }

    @app.get("/admin/reports/{report_id}/{kind}")
    def report_attachment(report_id: int, kind: str, token=Depends(admin_credentials)):
        with db.transaction() as tx:
            admin_subject(tx, token)
            row = tx.execute("SELECT content FROM report_attachment WHERE report_id=? AND kind=?",
                             (report_id, kind)).fetchone()
        if row is None:
            raise HTTPException(404)
        name = "screenshot.png" if kind == "screenshot" else "diagnostics.txt"
        return Response(row["content"], media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        })

    @app.post("/admin/action")
    def admin_action(body: AdminAction, token=Depends(admin_credentials)):
        sub, action = body.sub, body.action
        with db.transaction() as tx:
            actor = admin_subject(tx, token)
            guard_target(db, tx, actor, sub)
            result = {"ok": True}
            if action in {"minutes", "days"}:
                if body.operation_id is None:
                    raise HTTPException(422, "An operation ID is required for grants")
                if not record_operation(tx, actor, body.operation_id, body.model_dump_json(exclude={"operation_id"})):
                    return result
            if action == "invite":
                code = secrets.token_urlsafe(24)
                tx.execute("INSERT INTO invite(code,sub) VALUES(?,?)", (digest(code), sub))
                tx.execute("INSERT INTO invite_expiry VALUES(?,?)",
                           (digest(code), db.now() + body.invite_days * 86400))
                result["code"] = code
            elif action == "report":
                if body.status not in STATUSES:
                    raise HTTPException(422, "Invalid report status")
                changed = tx.execute("UPDATE report SET status=? WHERE id=? AND sub=?",
                                     (body.status, body.report_id, sub)).rowcount
                if not changed:
                    raise HTTPException(404, "Report not found")
            else:
                change_license(db, tx, sub, action, body.amount)
            db.audit(tx, actor, sub, f"{action} {body.amount} {body.report_id} {body.status}")
            return result

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():
        return HTMLResponse(Path(__file__).with_name("admin.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; script-src 'self'; "
                                     "style-src 'self'; connect-src 'self'; img-src blob:; "
                                     "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})

    @app.get("/admin/panel.css")
    def admin_style():
        return Response(Path(__file__).with_name("panel.css").read_text(encoding="utf-8"), media_type="text/css")

    @app.get("/admin/panel.js")
    def admin_script():
        return Response(Path(__file__).with_name("panel.js").read_text(encoding="utf-8"),
                        media_type="text/javascript")

    @app.post("/answer")
    def answer(body: Question, token=Depends(bearer)):
        api_key = os.getenv("EXAMAI_CLOUD_GEMINI_KEY")
        if not api_key:
            raise HTTPException(503, "Cloud Gemini is not configured")
        try:
            picture = base64.b64decode(body.image, validate=True) if body.image else None
        except ValueError:
            raise HTTPException(422, "Invalid image") from None

        request_id = secrets.token_hex(12)
        request_bytes = len(picture or b"") + len(body.question.encode()) + len(body.context.encode())
        # Bytes conservatively bound text tokens. Reserve additional capacity for
        # system instructions and one image, then settle against provider usage.
        estimate = len(body.question.encode()) + len(body.context.encode()) + 4096 + (4096 if picture else 0)
        cancelled = Event()
        deadline = monotonic() + cloud_queue.MAX_WAIT_SECONDS
        ticket = (request_id, request_bytes)
        used_models = set()
        charged_units = False

        def eligible(tx, sub):
            now = db.now()
            lease = tx.execute("SELECT token,until FROM lease WHERE sub=?", (sub,)).fetchone()
            active = lease is not None and lease["until"] > now
            if body.queue and active:
                raise HTTPException(409, "Stop the active session on the other client first")
            if not body.queue and not (active and lease["token"] == digest(token)):
                raise HTTPException(403, "Start an active beta session first")
            status = db.access(tx, sub, token=digest(token))
            if not status["granted"]:
                raise HTTPException(403, "Access or daily request limit reached")

        def admit(tx, sub):
            nonlocal charged_units
            now = db.now()
            pending = next(iter(waiting), None)
            if len(inflight) >= cloud_parallel or (pending is not None and (not body.queue or pending != sub)):
                return None, 1
            window = now - 60
            aggregate = tx.execute("SELECT count(*) FROM cloud_admission WHERE admitted>?", (window,)).fetchone()[0]
            if aggregate >= cloud_per_minute:
                return None, 1
            model, wait = cloud_queue.candidate(tx, now, estimate, used_models)
            if not model:
                return None, wait
            eligible(tx, sub)
            attempt = request_id + "-" + str(len(used_models))
            if budget:
                reservation = max(1, budget.estimate(estimate, 600)) if body.queue else None
                if not budget.reserve(tx, attempt, int(now // 86400), amount=reservation):
                    if body.queue and inflight:
                        return None, 1
                    raise HTTPException(429, "Cloud daily budget reached")
            if not charged_units:
                status = db.access(tx, sub, token=digest(token), units=2 if picture else 1)
                if not status["granted"]:
                    raise HTTPException(403, "Access or daily request limit reached")
            cloud_queue.reserve(tx, attempt, model, now, estimate)
            tx.execute("DELETE FROM cloud_admission WHERE admitted<=?", (window,))
            tx.execute("INSERT INTO cloud_admission VALUES(?,?)", (attempt, now))
            account = tx.execute("SELECT state,remaining FROM account WHERE sub=?", (sub,)).fetchone()
            duration = min(45, account["remaining"]) if account["state"] in {"BETA", "TRIAL"} else 45
            return (model, attempt, duration), 0

        with inflight_lock:
            with db.transaction() as tx:
                sub = subject(tx, token)
                if sub in waiting or sub in inflight:
                    raise HTTPException(409, "A cloud request is already queued or running")
                eligible(tx, sub)
                if body.queue:
                    now = monotonic()
                    stale = [identity for identity, timestamp in recent_submissions.items() if timestamp <= now - 2]
                    for identity in stale:
                        recent_submissions.pop(identity)
                    if sub in recent_submissions:
                        raise HTTPException(429, "Too many submissions", headers={"Retry-After": "2"})
                    if (len(waiting) >= cloud_queue.MAX_WAITING
                            or sum(size for _, size in waiting.values()) + request_bytes > cloud_queue.MAX_WAITING_BYTES):
                        raise HTTPException(429, "Cloud queue is full", headers={"Retry-After": "10"})
                    first_admission = None
                else:
                    first_admission, _ = admit(tx, sub)
                    if first_admission is None:
                        raise HTTPException(429, "Cloud is busy", headers={"Retry-After": "5"})
            if body.queue:
                waiting[sub] = ticket
                recent_submissions[sub] = now
            else:
                inflight[sub] = False

        def queued_admission():
            with inflight_lock:
                position = list(waiting).index(sub) + 1
                if position != 1 or len(inflight) >= cloud_parallel:
                    return None, position, 1
                with db.transaction() as tx:
                    subject(tx, token)
                    admission, delay = admit(tx, sub)
                if admission is not None:
                    waiting.pop(sub, None)
                    inflight[sub] = True
                position = list(waiting).index(sub) + 1 if sub in waiting else 0
                return admission, position, delay

        def generate(model, attempt, duration, events):
            usage = []
            complete = False
            streamed = False
            retryable = False
            retry_after = 0
            started = perf_counter()
            try:
                engine = LLMEngine(api_key)
                engine.gemini_model = model
                engine.strict_model = True
                engine.usage_callback = lambda prompt, output: usage.append((prompt, output))
                try:
                    def progress(text):
                        nonlocal streamed
                        streamed = True
                        if cancelled.is_set():
                            raise GenerationError("Cloud request cancelled")
                        events.put({"text": text})

                    limits = {"deadline": started + duration} if body.queue else {}
                    if picture:
                        final = engine.generate_image_answer(
                            picture, body.mime, on_text=progress, user_instruction=body.question,
                            context=body.context, cancelled=cancelled.is_set, language=body.language, **limits)
                    else:
                        final = engine.generate_answer(body.question, body.context, on_text=progress,
                                                       language=body.language, **limits)
                finally:
                    engine.close()
                terminal = {"final": final}
                complete = True
            except ProviderRateLimit as exc:
                retry_after = exc.retry_after
                retryable = not streamed
                terminal = {"error": "Gemini quota temporarily unavailable"}
            except ProviderUnavailable:
                retry_after = 5
                retryable = not streamed
                terminal = {"error": "Gemini temporarily unavailable"}
            except GenerationError as exc:
                terminal = {"error": str(exc)}
            except Exception as exc:
                log.warning("Cloud generation failed: %s", type(exc).__name__)
                terminal = {"error": "Cloud request failed"}
            finally:
                elapsed = perf_counter() - started
                retry = body.queue and retryable and len(used_models) < len(cloud_queue.MODELS) and not cancelled.is_set()
                with inflight_lock:
                    try:
                        with db.transaction() as tx:
                            cloud_queue.settle(tx, attempt, model, db.now(), sum(x for x, _ in usage), retry_after)
                            if budget:
                                if retryable and not usage:
                                    tx.execute("DELETE FROM cloud_charge WHERE id=?", (attempt,))
                                else:
                                    budget.settle(tx, attempt, sum(x for x, _ in usage),
                                                  sum(y for _, y in usage), complete, bool(usage))
                            if body.queue and (complete or streamed):
                                tx.execute("""UPDATE account SET remaining=max(0,remaining-?)
                                    WHERE sub=? AND state IN ('BETA','TRIAL')""", (min(duration, elapsed), sub))
                    except Exception as exc:
                        log.error("Cloud usage settlement failed: %s", type(exc).__name__)
                        retry = False
                    inflight.pop(sub, None)
                    if retry:
                        waiting[sub] = ticket
                events.put({"retry": True} if retry else terminal)

        async def stream():
            nonlocal charged_units
            admission = first_admission

            def line(event):
                return json.dumps(event, ensure_ascii=False) + "\n"

            try:
                while True:
                    if admission is None:
                        if monotonic() >= deadline:
                            yield line({"error_code": "queue_timeout", "error": "Cloud queue wait expired"})
                            break
                        try:
                            admission, position, delay = queued_admission()
                        except StoreBusy:
                            await asyncio.sleep(2)
                            continue
                        except HTTPException as exc:
                            yield line({"error": str(exc.detail)})
                            break
                        if admission is None:
                            if delay > deadline - monotonic() and position == 1:
                                yield line({"error_code": "quota_wait", "error": "Gemini quota temporarily unavailable"})
                                break
                            yield line({"queued": position})
                            await asyncio.sleep(min(max(delay, 0.25), 2))
                            continue

                    model, attempt, duration = admission
                    used_models.add(model)
                    charged_units = True
                    events = SimpleQueue()
                    Thread(target=generate, args=(model, attempt, duration, events), daemon=True).start()
                    if body.queue:
                        yield line({"started": True})
                    while True:
                        try:
                            event = events.get_nowait()
                        except Empty:
                            await asyncio.sleep(0.05)
                            continue
                        if "retry" in event:
                            admission = None
                            break
                        yield line(event)
                        if "final" in event or "error" in event:
                            return
            finally:
                with inflight_lock:
                    cancelled.set()
                    if waiting.get(sub) == ticket:
                        waiting.pop(sub)

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return app
