"""LiqPay sandbox checkout. No production credentials or recurring charges."""
import base64
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"


def signature(private, data):
    return base64.b64encode(hashlib.sha3_256((private + data + private).encode()).digest()).decode()


def mount(app, store, bearer, subject, plans, base, public, private):
    enabled = base.startswith("https://") and public.startswith("sandbox_") and bool(private)
    if not enabled:
        return False
    catalog = {plan["id"]: plan for plan in plans}
    with store.transaction() as tx:
        tx.execute("""CREATE TABLE IF NOT EXISTS payment (
          id TEXT PRIMARY KEY, sub TEXT, price INT, months INT,
          open_key TEXT UNIQUE, status TEXT DEFAULT 'pending', created REAL)""")

    @app.post("/payments/checkout/{plan_id}")
    def checkout(plan_id, token=Depends(bearer)):
        if plan_id not in catalog:
            raise HTTPException(404)
        plan = catalog[plan_id]
        order, nonce = secrets.token_hex(16), secrets.token_urlsafe(24)
        with store.transaction() as tx:
            sub = subject(tx, token)
            tx.execute("INSERT INTO payment(id,sub,price,months,open_key,created) VALUES(?,?,?,?,?,?)",
                       (order, sub, plan["price_uah"], plan["months"], nonce, store.now()))
        return {"url": f"{base}/payments/open/{nonce}"}

    @app.get("/payments/open/{nonce}", response_class=HTMLResponse)
    def open_checkout(nonce):
        with store.transaction() as tx:
            row = tx.execute("SELECT * FROM payment WHERE open_key=? AND created>? AND status='pending'",
                             (nonce, store.now() - 600)).fetchone()
        if not row:
            raise HTTPException(404)
        payload = dict(version=3, public_key=public, action="pay", amount=row["price"],
                       currency="UAH", description="EXAM AI sandbox", order_id=row["id"],
                       server_url=base + "/payments/callback")
        data = base64.b64encode(json.dumps(payload).encode()).decode()
        tag = signature(private, data)
        return HTMLResponse(f'<form method="post" action="{CHECKOUT_URL}">'
                            f'<input type="hidden" name="data" value="{data}">'
                            f'<input type="hidden" name="signature" value="{tag}">'
                            '<button>Continue to LiqPay sandbox</button></form>',
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.post("/payments/callback")
    async def callback(request: Request):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 24000:
                raise HTTPException(413)
        try:
            form = parse_qs(payload.decode(), max_num_fields=8, strict_parsing=True)
            data, tag = form["data"][0], form["signature"][0]
            if not hmac.compare_digest(signature(private, data), tag):
                raise ValueError
            event = json.loads(base64.b64decode(data, validate=True))
            status = event["status"]
            if (event["public_key"] != public or event["currency"] != "UAH" or event["action"] != "pay"
                    or status not in {"sandbox", "failure", "error", "reversed", "wait_accept"}):
                raise ValueError
            with store.transaction() as tx:
                row = tx.execute("SELECT * FROM payment WHERE id=?", (event["order_id"],)).fetchone()
                if not row or Decimal(str(event["amount"])) != row["price"]:
                    raise ValueError
                previous = row["status"]
                if previous == status or previous == "reversed" or (previous == "sandbox" and status != "reversed"):
                    return {"ok": True}
                # Sandbox purchases are recorded separately and never unlock production access.
                tx.execute("UPDATE payment SET status=? WHERE id=?", (status, row["id"]))
                store.audit(tx, "liqpay_sandbox", row["sub"], f"payment {row['id']} {status}")
        except (ValueError, InvalidOperation, TypeError, KeyError):
            raise HTTPException(400, "Invalid sandbox callback") from None
        return {"ok": True}

    @app.get("/payments")
    def history(token=Depends(bearer)):
        with store.transaction() as tx:
            sub = subject(tx, token)
            return [dict(row) for row in tx.execute(
                "SELECT id, price, months, status, created FROM payment WHERE sub=? ORDER BY created DESC LIMIT 50",
                (sub,))]
    return True
