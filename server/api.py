"""EXAM AI beta service. Run behind HTTPS with one persistent SQLite volume."""
import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode, urlsplit
from threading import Event, Lock, Thread

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token
from pydantic import BaseModel, Field

from examai.ai.llm_engine import LLMEngine
from server.payments import mount as mount_payments
from server.store import Denied, Store, digest


PLANS = tuple(dict(id=str(months), months=months, price_uah=price)
              for months, price in ((1, 149), (3, 399), (6, 699), (12, 1299)))
STATUSES = {"Received", "Reviewing", "Confirmed", "Fixed", "Closed"}


class Device(BaseModel):
    device: str = Field(min_length=16, max_length=128)


class Claim(BaseModel):
    claim: str = Field(min_length=32, max_length=128)


class Invite(BaseModel):
    code: str = Field(min_length=16, max_length=128)


class Problem(BaseModel):
    kind: str = Field(pattern="^(Bug|Suggestion)$")
    description: str = Field(min_length=5, max_length=4000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=3500)
    image: str = Field(default="", max_length=3_500_000)
    mime: str = Field(default="image/png", pattern="^image/(png|jpeg)$")


class AdminAction(BaseModel):
    sub: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern="^(invite|minutes|days|suspend|report)$")
    amount: int = Field(default=0, ge=0, le=365)
    report_id: int = 0
    status: str = ""


def create_app(path=None):
    app = FastAPI(title="EXAM AI Closed Beta")
    db = Store(path or os.getenv("EXAMAI_SERVER_DB", "examai-server.db"))
    admins = set(filter(None, os.getenv("EXAMAI_ADMIN_SUBJECTS", "").split(",")))
    client_id = os.getenv("EXAMAI_GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("EXAMAI_GOOGLE_CLIENT_SECRET", "")
    base = os.getenv("EXAMAI_PUBLIC_URL", "").rstrip("/")
    redirect = base + "/oauth/callback"
    inflight = set()
    inflight_lock = Lock()

    def bearer(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 128:
            raise HTTPException(401, "Sign in to EXAM AI")
        return authorization[7:]

    def subject(tx, token):
        try:
            return db.authenticate(tx, token)
        except Denied as exc:
            raise HTTPException(401, str(exc)) from None

    def admin_subject(tx, token):
        sub = subject(tx, token)
        if sub not in admins:
            raise HTTPException(403, "Administrator required")
        return sub

    @app.post("/oauth/start")
    def oauth_start(body: Device):
        if not client_id or not client_secret or urlsplit(base).scheme != "https":
            raise HTTPException(503, "Google Sign-In is not configured on the server")
        state, verifier, nonce, claim = (secrets.token_urlsafe(32) for _ in range(4))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with db.transaction() as tx:
            tx.execute("DELETE FROM oauth WHERE until <= ?", (db.now(),))
            tx.execute("INSERT INTO oauth VALUES (?,?,?,?,?,?,NULL)",
                       (state, verifier, nonce, digest(claim), body.device, db.now() + 300))
        query = urlencode(dict(client_id=client_id, redirect_uri=redirect, response_type="code",
                               scope="openid", state=state, nonce=nonce, code_challenge=challenge,
                               code_challenge_method="S256"))
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + query, "claim": claim}

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(state: str, code: str):
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
            claims = verify_oauth2_token(response.json()["id_token"], Request(), client_id)
            if claims.get("nonce") != row["nonce"] or not claims.get("sub"):
                raise ValueError("Invalid OIDC response")
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(400, "Google authentication failed") from None
        with db.transaction() as tx:
            tx.execute("UPDATE oauth SET sub=? WHERE state=?", (claims["sub"], state))
        return "EXAM AI: sign-in confirmed. Return to the desktop app."

    @app.post("/oauth/claim")
    def oauth_claim(body: Claim):
        with db.transaction() as tx:
            row = tx.execute("SELECT * FROM oauth WHERE claim=? AND until>?",
                             (digest(body.claim), db.now())).fetchone()
            if not row:
                raise HTTPException(410, "Login expired")
            if not row["sub"]:
                return {"pending": True}
            try:
                token = db.issue(tx, row["sub"], row["device"])
            except Denied as exc:
                raise HTTPException(409, str(exc)) from None
            tx.execute("DELETE FROM oauth WHERE claim=?", (digest(body.claim),))
            return {"token": token}

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
            return db.access(tx, sub, token=digest(token))

    @app.post("/invite")
    def invite(body: Invite, token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            try:
                db.redeem(tx, sub, body.code)
            except Denied as exc:
                raise HTTPException(409, str(exc)) from None
            return db.access(tx, sub, token=digest(token))

    @app.post("/activity/{action}")
    def activity(action: str, token=Depends(bearer)):
        if action not in {"start", "stop"}:
            raise HTTPException(404)
        with db.transaction() as tx:
            return db.access(tx, subject(tx, token), token=digest(token), lease_action=action)

    checkout_enabled = mount_payments(app, db, bearer, subject, PLANS, base,
                                      os.getenv("EXAMAI_LIQPAY_SANDBOX_PUBLIC", ""),
                                      os.getenv("EXAMAI_LIQPAY_SANDBOX_PRIVATE", ""))

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/plans")
    def plans():
        return {"plans": PLANS, "checkout_available": checkout_enabled}

    @app.post("/reports")
    def report(body: Problem, token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            count = tx.execute("SELECT count(*) FROM report WHERE sub=? AND created>?",
                               (sub, db.now() - 86400)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, "Daily report limit reached")
            row = tx.execute("INSERT INTO report(sub,kind,description,created) VALUES(?,?,?,?)",
                             (sub, body.kind, body.description, db.now()))
            return {"id": row.lastrowid, "status": "Received"}

    @app.get("/reports")
    def reports(token=Depends(bearer)):
        with db.transaction() as tx:
            sub = subject(tx, token)
            return [dict(row) for row in tx.execute(
                "SELECT id,kind,status,created FROM report WHERE sub=? ORDER BY id DESC LIMIT 100", (sub,))]

    @app.get("/admin/data")
    def admin_data(q: str = "", token=Depends(bearer)):
        with db.transaction() as tx:
            admin_subject(tx, token)
            return {
                "users": [dict(row) for row in tx.execute(
                    "SELECT * FROM account WHERE instr(sub, ?) > 0 ORDER BY sub LIMIT 100", (q,))],
                "reports": [dict(row) for row in tx.execute("SELECT * FROM report ORDER BY id DESC LIMIT 100")],
                "audit": [dict(row) for row in tx.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")],
            }

    @app.post("/admin/action")
    def admin_action(body: AdminAction, token=Depends(bearer)):
        sub, action = body.sub, body.action
        with db.transaction() as tx:
            actor = admin_subject(tx, token)
            result = {"ok": True}
            if action == "invite":
                code = secrets.token_urlsafe(24)
                tx.execute("INSERT INTO invite(code,sub) VALUES(?,?)", (digest(code), sub))
                result["code"] = code
            elif action == "report" and body.status in STATUSES:
                changed = tx.execute("UPDATE report SET status=? WHERE id=? AND sub=?",
                                     (body.status, body.report_id, sub)).rowcount
                if not changed:
                    raise HTTPException(404, "Report not found")
            else:
                if not tx.execute("SELECT 1 FROM account WHERE sub=?", (sub,)).fetchone():
                    raise HTTPException(404, "Account not found")
                db.access(tx, sub)
                if action == "minutes" and body.amount:
                    tx.execute("UPDATE account SET remaining=remaining+?, state=CASE WHEN state='ACTIVE' "
                               "THEN state ELSE 'BETA' END WHERE sub=?", (body.amount * 60, sub))
                elif action == "days" and body.amount:
                    tx.execute("UPDATE account SET state='ACTIVE', until=max(?,coalesce(until,0))+? WHERE sub=?",
                               (db.now(), body.amount * 86400, sub))
                elif action == "suspend":
                    tx.execute("UPDATE account SET state='SUSPENDED' WHERE sub=?", (sub,))
                    tx.execute("DELETE FROM lease WHERE sub=?", (sub,))
                else:
                    raise HTTPException(422, "Invalid admin action")
            db.audit(tx, actor, sub, f"{action} {body.amount} {body.report_id} {body.status}")
            return result

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():
        from pathlib import Path
        return HTMLResponse(Path(__file__).with_name("admin.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Frame-Options": "DENY"})

    @app.post("/answer")
    def answer(body: Question, token=Depends(bearer)):
        api_key = os.getenv("EXAMAI_CLOUD_GEMINI_KEY")
        if not api_key:
            raise HTTPException(503, "Cloud Gemini is not configured")
        try:
            picture = base64.b64decode(body.image, validate=True) if body.image else None
        except ValueError:
            raise HTTPException(422, "Invalid image") from None
        with db.transaction() as tx:
            sub = subject(tx, token)
            active = tx.execute("SELECT until FROM lease WHERE sub=? AND token=?",
                                (sub, digest(token))).fetchone()
            if not active or active[0] <= db.now():
                raise HTTPException(403, "Start an active beta session first")
            with inflight_lock:
                if sub in inflight:
                    raise HTTPException(409, "A cloud request is already running")
                status = db.access(tx, sub, token=digest(token), units=2 if picture else 1)
                if status["granted"]:
                    inflight.add(sub)
        if not status["granted"]:
            raise HTTPException(403, "Access or daily request limit reached")
        from queue import Queue

        queue = Queue()
        cancelled = Event()
        def generate():
            engine = LLMEngine(api_key)
            try:
                def progress(text):
                    if cancelled.is_set():
                        from examai.ai.llm_engine import GenerationError
                        raise GenerationError("Cloud request cancelled")
                    queue.put({"text": text})
                if picture:
                    final = engine.generate_image_answer(picture, body.mime, on_text=progress,
                                                         user_instruction=body.question, context=body.context)
                else:
                    final = engine.generate_answer(body.question, body.context, on_text=progress)
                queue.put({"final": final})
            except Exception:
                queue.put({"error": "Cloud request failed"})
            finally:
                try:
                    engine.close()
                finally:
                    with inflight_lock:
                        inflight.discard(sub)
                    queue.put(None)

        def stream():
            Thread(target=generate, daemon=True).start()
            try:
                while (event := queue.get()) is not None:
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                cancelled.set()
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app
