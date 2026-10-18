"""PayPal REST API client (SANDBOX ONLY).

Implements exactly the PayPal REST flows we need, per the official docs:
- OAuth2 client-credentials token: POST /v1/oauth2/token
- Orders v2: create, get, capture
- Payments v2: get capture, refund capture, get refund
- Disputes v1: list, get, provide-evidence, accept-claim

Money-moving POST calls always send a PayPal-Request-Id header
(idempotency, per https://developer.paypal.com/api/rest/reference/idempotency/).

Secrets are NEVER logged or printed by this module.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Optional

import httpx

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


class PayPalError(Exception):
    """Raised for any PayPal API failure."""

    def __init__(
        self,
        message: str,
        http_status: Optional[int] = None,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.details = details or {}


class PayPalClient:
    """Thin, honest wrapper around the PayPal REST API. Sandbox only.

    Args:
        client_id: Sandbox app client ID (from the developer dashboard).
        client_secret: Sandbox app secret. Never logged, never printed.
        mode: Must be "sandbox". Anything else raises ValueError.
    """

    def __init__(self, client_id: str, client_secret: str, mode: str = "sandbox") -> None:
        if mode != "sandbox":
            raise ValueError(
                f"PayPalClient refuses mode={mode!r}: this project is sandbox-only."
            )
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required.")
        self._client_id = client_id
        self._client_secret = client_secret
        self.base_url = SANDBOX_BASE_URL
        # Some environments export no_proxy/NO_PROXY with bracketed IPv6
        # entries (e.g. "[::1]") that crash httpx's proxy URL parsing
        # (httpx.InvalidURL: Invalid port: ':1]'). This client only talks
        # to the external sandbox API, which must go through the egress
        # proxy anyway, so drop the bypass list before building the client.
        import os as _os

        for _var in ("no_proxy", "NO_PROXY"):
            _os.environ.pop(_var, None)
        self._http = httpx.Client(base_url=self.base_url, timeout=15.0)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------ auth

    def _get_token(self) -> str:
        """Return a cached bearer token, refreshing it when expired."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        resp = self._http.post(
            "/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content="grant_type=client_credentials",
        )
        if resp.status_code != 200:
            raise PayPalError(
                "Failed to obtain PayPal access token.",
                http_status=resp.status_code,
                details=_safe_body(resp),
            )
        data = resp.json()
        self._token = data["access_token"]
        # expires_in is seconds; cache a little short of it.
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        paypal_request_id: Optional[str] = None,
    ) -> dict:
        """Send an authenticated request; raise PayPalError on failure."""
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }
        if paypal_request_id:
            headers["PayPal-Request-Id"] = paypal_request_id
        resp = self._http.request(method, path, headers=headers, json=json)
        if resp.status_code >= 400:
            raise PayPalError(
                f"PayPal API {method} {path} failed.",
                http_status=resp.status_code,
                details=_safe_body(resp),
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ----------------------------------------------------------------- orders

    def create_order(
        self, amount: str, currency: str = "USD", description: str = ""
    ) -> dict:
        """Create an order with intent CAPTURE. Returns the order incl. approve link."""
        body: dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {"currency_code": currency, "value": amount},
                    "description": description or "PayPal Pulse AI test order",
                }
            ],
        }
        return self._request(
            "POST",
            "/v2/checkout/orders",
            json=body,
            paypal_request_id=str(uuid.uuid4()),
        )

    def get_order(self, order_id: str) -> dict:
        """Fetch order details."""
        return self._request("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str, paypal_request_id: str) -> dict:
        """Capture an approved order. Moves money: idempotency header required."""
        return self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            json={},
            paypal_request_id=paypal_request_id,
        )

    # ---------------------------------------------------------------- payments

    def get_capture(self, capture_id: str) -> dict:
        """Fetch capture details (status: COMPLETED/PENDING/DENIED/REFUNDED...)."""
        return self._request("GET", f"/v2/payments/captures/{capture_id}")

    def refund_capture(
        self,
        capture_id: str,
        amount: Optional[str] = None,
        currency: str = "USD",
        note: Optional[str] = None,
        paypal_request_id: Optional[str] = None,
    ) -> dict:
        """Refund a capture (full if amount is None). Moves money: idempotent."""
        body: dict[str, Any] = {}
        if amount is not None:
            body["amount"] = {"currency_code": currency, "value": amount}
        if note:
            body["note_to_payer"] = note
        return self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json=body,
            paypal_request_id=paypal_request_id or str(uuid.uuid4()),
        )

    def get_refund(self, refund_id: str) -> dict:
        """Fetch refund details (used to verify a refund actually happened)."""
        return self._request("GET", f"/v2/payments/refunds/{refund_id}")

    # ---------------------------------------------------------------- disputes

    def list_disputes(self) -> dict:
        """List disputes for the merchant account."""
        return self._request("GET", "/v1/customer/disputes")

    def get_dispute(self, dispute_id: str) -> dict:
        """Fetch dispute details (status, reason, deadlines, messages)."""
        return self._request("GET", f"/v1/customer/disputes/{dispute_id}")

    def provide_evidence(self, dispute_id: str, evidence: dict) -> dict:
        """Submit evidence for a dispute. Sensitive action: gate + human approval first."""
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            json=evidence,
            paypal_request_id=str(uuid.uuid4()),
        )

    def accept_claim(self, dispute_id: str, note: str = "") -> dict:
        """Accept a dispute claim (refunds the buyer). Sensitive action."""
        body: dict[str, Any] = {}
        if note:
            body["note"] = note
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/accept-claim",
            json=body,
            paypal_request_id=str(uuid.uuid4()),
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._http.close()


def _safe_body(resp: httpx.Response) -> dict:
    """Extract an error body without ever including credentials."""
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": str(data)[:500]}
    except Exception:
        return {"raw": (resp.text or "")[:500]}
