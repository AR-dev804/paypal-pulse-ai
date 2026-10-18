"""In-memory fake PayPal client for offline development and tests.

Mirrors PayPalClient's method signatures exactly, but stores everything in
memory with deterministic IDs ("ORDER-1", "CAPTURE-1", ...). No network,
no secrets, fully deterministic.

Supports:
- order create / approve-simulate / capture with configurable outcome
  (COMPLETED / PENDING / DENIED)
- capture get
- refund with idempotency: the same PayPal-Request-Id returns the SAME
  refund object and creates only one refund; partial refunds allowed
- disputes: list / get / provide-evidence / accept-claim

This fake is honest: it never pretends to be the real API. Anything that
needs the real sandbox must use PayPalClient.
"""

from __future__ import annotations

from typing import Any, Optional

from .paypal_client import PayPalError


class FakePayPalClient:
    """Deterministic in-memory stand-in for PayPalClient."""

    def __init__(self, capture_outcome: str = "COMPLETED") -> None:
        if capture_outcome not in ("COMPLETED", "PENDING", "DENIED"):
            raise ValueError(f"bad capture_outcome: {capture_outcome!r}")
        self.capture_outcome = capture_outcome
        self._seq = 0
        self.orders: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self._refund_idempotency: dict[str, str] = {}  # request-id -> refund id
        self.disputes: dict[str, dict] = {}
        self._seed_dispute()

    # ------------------------------------------------------------ internals

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _seed_dispute(self) -> None:
        dispute_id = self._next("DISPUTE")
        self.disputes[dispute_id] = {
            "dispute_id": dispute_id,
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "dispute_amount": {"currency_code": "USD", "value": "25.00"},
            "response_due_date": "2026-11-20T00:00:00Z",
            "messages": [
                {
                    "posted_by": "BUYER",
                    "content": "Item never arrived, I want my money back.",
                }
            ],
            "evidence": [],
        }

    # ----------------------------------------------------------------- orders

    def create_order(
        self, amount: str, currency: str = "USD", description: str = ""
    ) -> dict:
        order_id = self._next("ORDER")
        order = {
            "id": order_id,
            "status": "CREATED",
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {"currency_code": currency, "value": amount},
                    "description": description or "PayPal Pulse AI test order",
                }
            ],
            "links": [
                {
                    "rel": "approve",
                    "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                }
            ],
        }
        self.orders[order_id] = order
        return order

    def approve_order(self, order_id: str) -> dict:
        """Simulate the buyer approving the order (no real PayPal page)."""
        order = self.orders.get(order_id)
        if order is None:
            raise PayPalError(f"Unknown order {order_id}", http_status=404)
        order["status"] = "APPROVED"
        return order

    def get_order(self, order_id: str) -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise PayPalError(f"Unknown order {order_id}", http_status=404)
        return order

    def capture_order(self, order_id: str, paypal_request_id: str) -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise PayPalError(f"Unknown order {order_id}", http_status=404)
        if order["status"] != "APPROVED":
            raise PayPalError(
                f"Order {order_id} is {order['status']}, must be APPROVED to capture.",
                http_status=422,
            )
        amount = order["purchase_units"][0]["amount"]
        capture_id = self._next("CAPTURE")
        capture = {
            "id": capture_id,
            "status": self.capture_outcome,
            "amount": dict(amount),
            "order_id": order_id,
            "refunded_amount": {"currency_code": amount["currency_code"], "value": "0.00"},
        }
        self.captures[capture_id] = capture
        order["status"] = "COMPLETED"
        return capture

    # ---------------------------------------------------------------- payments

    def get_capture(self, capture_id: str) -> dict:
        capture = self.captures.get(capture_id)
        if capture is None:
            raise PayPalError(f"Unknown capture {capture_id}", http_status=404)
        return capture

    def refund_capture(
        self,
        capture_id: str,
        amount: Optional[str] = None,
        currency: str = "USD",
        note: Optional[str] = None,
        paypal_request_id: Optional[str] = None,
    ) -> dict:
        capture = self.captures.get(capture_id)
        if capture is None:
            raise PayPalError(f"Unknown capture {capture_id}", http_status=404)
        key = paypal_request_id or f"auto-{self._seq + 1}"
        # Idempotency: same key -> same refund object, no duplicate.
        if key in self._refund_idempotency:
            return self.refunds[self._refund_idempotency[key]]
        if capture["status"] != "COMPLETED":
            raise PayPalError(
                f"Capture {capture_id} is {capture['status']}; only COMPLETED "
                "captures can be refunded.",
                http_status=422,
            )
        captured_value = float(capture["amount"]["value"])
        refund_value = float(amount) if amount is not None else captured_value
        already = float(capture["refunded_amount"]["value"])
        if refund_value <= 0 or already + refund_value > captured_value + 1e-9:
            raise PayPalError(
                f"Invalid refund amount {refund_value} for capture {capture_id}.",
                http_status=422,
            )
        refund_id = self._next("REFUND")
        refund: dict[str, Any] = {
            "id": refund_id,
            "status": "COMPLETED",
            "amount": {"currency_code": currency, "value": f"{refund_value:.2f}"},
            "capture_id": capture_id,
        }
        if note:
            refund["note_to_payer"] = note
        self.refunds[refund_id] = refund
        self._refund_idempotency[key] = refund_id
        capture["refunded_amount"]["value"] = f"{already + refund_value:.2f}"
        if abs(already + refund_value - captured_value) < 1e-9:
            capture["status"] = "REFUNDED"
        return refund

    def get_refund(self, refund_id: str) -> dict:
        refund = self.refunds.get(refund_id)
        if refund is None:
            raise PayPalError(f"Unknown refund {refund_id}", http_status=404)
        return refund

    # ---------------------------------------------------------------- disputes

    def list_disputes(self) -> dict:
        return {"items": list(self.disputes.values())}

    def get_dispute(self, dispute_id: str) -> dict:
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise PayPalError(f"Unknown dispute {dispute_id}", http_status=404)
        return dispute

    def provide_evidence(self, dispute_id: str, evidence: dict) -> dict:
        dispute = self.get_dispute(dispute_id)
        if dispute["status"] != "WAITING_FOR_SELLER_RESPONSE":
            raise PayPalError(
                f"Dispute {dispute_id} is {dispute['status']}; evidence only "
                "accepted while WAITING_FOR_SELLER_RESPONSE.",
                http_status=422,
            )
        dispute["evidence"].append(evidence)
        dispute["status"] = "UNDER_REVIEW"
        return {"dispute_id": dispute_id, "status": dispute["status"]}

    def accept_claim(self, dispute_id: str, note: str = "") -> dict:
        dispute = self.get_dispute(dispute_id)
        if dispute["status"] == "RESOLVED":
            raise PayPalError(
                f"Dispute {dispute_id} already RESOLVED.", http_status=422
            )
        dispute["status"] = "RESOLVED"
        dispute["resolution"] = {"note": note, "outcome": "BUYER_FAVOR"}
        return {"dispute_id": dispute_id, "status": "RESOLVED"}

    def close(self) -> None:
        """No-op: kept for signature parity with PayPalClient."""
