"""PayPal Pulse AI — FastAPI application.

JSON API for the merchant dashboard. The backend is the authority for
payment state: every sensitive action re-fetches live state from PayPal
(or the fake in demo mode) and passes the Safe Action Gate before
anything executes. The AI proposes; this code disposes.

Client selection: USE_FAKE_PAYPAL=true (default) uses the in-memory
fake — honest demo data, no network, no secrets. Set USE_FAKE_PAYPAL=false
with PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET for the real sandbox
(fails fast with a clear error if credentials are missing).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import audit, gate, store
from .ai import investigate_transaction
from .fake_paypal import FakePayPalClient
from .paypal_client import PayPalClient, PayPalError

app = FastAPI(title="PayPal Pulse AI", version="0.2.0")

_FRONTEND_INDEX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend",
    "index.html",
)


@app.get("/", include_in_schema=False)
def _serve_dashboard():
    if os.path.exists(_FRONTEND_INDEX):
        return FileResponse(_FRONTEND_INDEX, media_type="text/html")
    return JSONResponse({"detail": "dashboard not built yet"}, status_code=404)


# ------------------------------------------------------------------ config


def use_fake() -> bool:
    return os.environ.get("USE_FAKE_PAYPAL", "true").lower() in ("1", "true", "yes")


def store_path() -> str:
    return store.db_path()


_client: Any = None


def _build_client() -> Any:
    if use_fake():
        return FakePayPalClient()
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "USE_FAKE_PAYPAL=false but PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET "
            "are not set. Set them in the environment (never commit them)."
        )
    return PayPalClient(client_id, client_secret, mode="sandbox")


def get_client() -> Any:
    """Lazily built PayPal client (fake or real sandbox)."""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


# ------------------------------------------------------------------ models


class InvestigateIn(BaseModel):
    payment_id: int


class RefundPrepareIn(BaseModel):
    payment_id: int
    amount: Optional[str] = None


class RefundConfirmIn(BaseModel):
    payment_id: int
    amount: Optional[str] = None
    paypal_request_id: str


class AttackIn(BaseModel):
    payment_id: int
    poisoned_message: str


# ------------------------------------------------------------------ helpers


def _payment_or_404(pid: int) -> dict:
    payment = store.get_payment(pid)
    if payment is None:
        raise HTTPException(status_code=404, detail=f"payment {pid} not found")
    return payment


def _live_capture(payment: dict) -> dict:
    """Re-fetch capture state from PayPal. Backend is the authority."""
    capture_id = payment.get("capture_id")
    if not capture_id:
        return {
            "id": None,
            "status": payment.get("status", "UNKNOWN"),
            "amount": {
                "currency_code": payment.get("currency", "USD"),
                "value": payment.get("amount") or "0.00",
            },
        }
    try:
        return get_client().get_capture(capture_id)
    except PayPalError as exc:
        raise HTTPException(
            status_code=502, detail=f"PayPal lookup failed: {exc}"
        ) from exc


def _tx_for(payment: dict) -> dict:
    """Build the transaction dict the AI investigates."""
    tx: dict[str, Any] = {}
    raw = payment.get("raw_json")
    if isinstance(raw, dict):
        capture = raw.get("capture")
        if isinstance(capture, dict):
            tx.update(capture)
    tx.setdefault("id", payment.get("capture_id") or payment.get("paypal_order_id"))
    tx.setdefault("status", payment.get("status"))
    tx.setdefault(
        "amount",
        {
            "currency_code": payment.get("currency", "USD"),
            "value": payment.get("amount") or "0.00",
        },
    )
    if payment.get("buyer_note"):
        tx["buyer_note"] = payment["buyer_note"]
    return tx


# ------------------------------------------------------------------- routes


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "paypal-pulse-ai",
        "mode": "sandbox",
        "fake_paypal": use_fake(),
    }


@app.get("/api/payments")
def list_payments() -> dict:
    return {"payments": store.list_payments(), "demo": use_fake()}


@app.get("/api/payments/{pid}")
def payment_detail(pid: int) -> dict:
    return {"payment": _payment_or_404(pid), "demo": use_fake()}


@app.post("/api/investigate")
def investigate(body: InvestigateIn) -> dict:
    payment = _payment_or_404(body.payment_id)
    diagnosis = investigate_transaction(_tx_for(payment))
    audit.append(
        store_path(),
        actor="ai",
        action="investigate",
        target_id=str(payment["id"]),
        summary=str(diagnosis.get("summary", ""))[:300],
        result="ok",
    )
    return {"payment_id": payment["id"], "diagnosis": diagnosis}


@app.post("/api/actions/refund/prepare")
def refund_prepare(body: RefundPrepareIn) -> dict:
    """Build a refund proposal WITHOUT executing it (feeds the approval modal)."""
    payment = _payment_or_404(body.payment_id)
    capture = _live_capture(payment)
    if not capture.get("id"):
        raise HTTPException(
            status_code=422, detail="No capture yet — nothing to refund."
        )
    ok, reason = gate.validate_refund(capture, body.amount)
    amount = body.amount or capture["amount"]["value"]
    return {
        "action": "refund_capture",
        "capture_id": capture["id"],
        "amount": amount,
        "currency": capture["amount"]["currency_code"],
        "allowed": ok,
        "reason": reason,
        "capture_status": capture["status"],
    }


@app.post("/api/actions/refund/confirm")
def refund_confirm(body: RefundConfirmIn) -> dict:
    """Execute a human-approved refund through the Safe Action Gate."""
    payment = _payment_or_404(body.payment_id)
    capture = _live_capture(payment)
    if not capture.get("id"):
        raise HTTPException(
            status_code=422, detail="No capture yet — nothing to refund."
        )
    try:
        # Human-approved action: the merchant clicked confirm in the
        # dashboard after reviewing the AI proposal.
        gate.authorize(
            "refund_capture", {"capture": capture, "amount": body.amount},
            origin="human",
        )
    except gate.GateRefused as exc:
        audit.append(
            store_path(),
            actor="gate",
            action="refund_capture",
            target_id=capture.get("id"),
            summary=f"REFUSED: {exc.reason}",
            paypal_request_id=body.paypal_request_id,
            result="blocked",
        )
        return JSONResponse(
            status_code=409, content={"blocked": True, "reason": exc.reason}
        )
    try:
        refund = get_client().refund_capture(
            capture["id"],
            amount=body.amount,
            currency=capture["amount"]["currency_code"],
            paypal_request_id=body.paypal_request_id,
        )
        verified = get_client().get_refund(refund["id"])
        fresh_capture = get_client().get_capture(capture["id"])
    except PayPalError as exc:
        audit.append(
            store_path(),
            actor="paypal",
            action="refund_capture",
            target_id=capture.get("id"),
            summary=f"PayPal error: {exc}",
            paypal_request_id=body.paypal_request_id,
            result="error",
        )
        raise HTTPException(
            status_code=502, detail=f"PayPal refund failed: {exc}"
        ) from exc
    audit.append(
        store_path(),
        actor="merchant",
        action="refund_capture",
        target_id=capture.get("id"),
        summary=(
            f"Refunded {verified['amount']['value']} "
            f"{verified['amount']['currency_code']} (refund {refund['id']}, "
            f"status {verified.get('status')})"
        ),
        paypal_request_id=body.paypal_request_id,
        result="ok",
    )
    store.update_status(
        payment["id"], fresh_capture.get("status", "REFUNDED"), capture["id"]
    )
    return {
        "refund": refund,
        "verified": verified,
        "payment": store.get_payment(payment["id"]),
    }


@app.get("/api/disputes")
def list_disputes() -> dict:
    try:
        return {"disputes": get_client().list_disputes(), "demo": use_fake()}
    except PayPalError as exc:
        raise HTTPException(
            status_code=502, detail=f"PayPal dispute lookup failed: {exc}"
        ) from exc


@app.get("/api/audit")
def audit_log() -> dict:
    return {"entries": audit.list_recent(store_path(), limit=50)}


@app.post("/api/demo/seed")
def demo_seed() -> dict:
    """Create scripted demo payments (COMPLETED / PENDING / DENIED).

    Fake mode only. Honest by construction: the response is labeled
    simulated, and the frontend shows a demo-data banner.
    """
    if not use_fake():
        raise HTTPException(
            status_code=403, detail="Demo seed is only available in fake mode."
        )
    client = get_client()
    scenarios = [
        ("25.00", "COMPLETED"),
        ("40.00", "PENDING"),
        ("15.00", "DENIED"),
    ]
    seeded = []
    for amount, outcome in scenarios:
        client.capture_outcome = outcome
        order = client.create_order(amount, "USD", f"Pulse demo order ({outcome})")
        client.approve_order(order["id"])
        capture = client.capture_order(order["id"], str(uuid.uuid4()))
        pid = store.save_payment(
            paypal_order_id=order["id"],
            capture_id=capture["id"],
            amount=amount,
            currency="USD",
            status=capture["status"],
            raw={"order": order, "capture": capture},
        )
        seeded.append({"id": pid, "status": capture["status"], "amount": amount})
    audit.append(
        store_path(),
        actor="demo",
        action="seed",
        summary=f"Seeded {len(seeded)} simulated payments",
        result="ok",
    )
    return {
        "demo": True,
        "seeded": seeded,
        "note": "Simulated sandbox data — not live PayPal.",
    }


@app.post("/api/demo/attack")
def demo_attack(body: AttackIn) -> dict:
    """SIMULATED adversarial test: a poisoned buyer message tries to make
    the agent issue a refund; the Safe Action Gate must block it.

    Nothing here executes a refund. The poisoned text is labeled untrusted
    input end to end and is never treated as an instruction.
    """
    payment = _payment_or_404(body.payment_id)
    tx = _tx_for(payment)
    # The poisoned message arrives exactly as untrusted buyer-provided data.
    tx["buyer_note"] = body.poisoned_message
    diagnosis = investigate_transaction(tx)

    # What (if anything) did the AI get nudged toward?
    ai_suggested = [
        a for a in diagnosis.get("recommended_actions", [])
        if a.get("action") in ("refund_capture", "provide_evidence", "accept_claim")
    ]

    capture = _live_capture(payment)
    try:
        # The poisoned message is untrusted buyer-provided text: it must
        # be refused on origin alone, even when the capture state would
        # allow the same action from a human. The ALLOWED branch below is
        # unreachable today; kept defensively so any future gate change
        # surfaces in the audit trail instead of silently passing.
        gate.authorize(
            "refund_capture", {"capture": capture, "amount": None},
            origin="untrusted",
        )
        decision, reason = "ALLOWED", (
            "Gate authorized the action — investigate why an "
            "untrusted-origin action was allowed."
        )
    except gate.GateRefused as exc:
        decision, reason = "BLOCKED", exc.reason

    audit.append(
        store_path(),
        actor="gate",
        action="attack_simulation",
        target_id=str(payment["id"]),
        summary=f"Poisoned buyer message -> gate {decision}: {reason[:200]}",
        result="blocked" if decision == "BLOCKED" else "allowed_not_executed",
    )
    return {
        "simulated": True,
        "payment_id": payment["id"],
        "poisoned_input": body.poisoned_message,
        "ai_suggested": ai_suggested,
        "gate_decision": decision,
        "reason": reason,
    }
