#!/usr/bin/env python3
"""Real-sandbox refund spike for PayPal Pulse AI.

Runs the money loop step by step against the PayPal SANDBOX:
  1. Get OAuth token (prints OK only -- the token is NEVER printed).
  2. Create a $1.00 order, print the approval URL + order id.
     >>> HUMAN STEP: open the approval URL, log in with the sandbox PERSONAL
     >>> (buyer) test account, and approve the payment. <<<
  3. --capture ORDER_ID : capture the approved order, print capture id + status.
  4. --refund CAPTURE_ID [--amount X.XX] : refund with a generated
     PayPal-Request-Id, print refund id + status, then verify via get_refund.

Each step is non-interactive. Exit code 0 on success, non-zero on failure.
Requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in the environment.

Usage:
    python scripts/spike_refund.py
    python scripts/spike_refund.py --capture ORDER_ID
    python scripts/spike_refund.py --refund CAPTURE_ID --amount 0.50
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paypal_client import PayPalClient, PayPalError  # noqa: E402


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", metavar="ORDER_ID",
                        help="Capture an already-approved order.")
    parser.add_argument("--refund", metavar="CAPTURE_ID",
                        help="Refund a completed capture.")
    parser.add_argument("--amount", default=None,
                        help="Partial refund amount (default: full refund).")
    args = parser.parse_args()

    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    if not client_id or not client_secret:
        return fail(
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in the "
            "environment. Copy .env.example to .env and fill in your SANDBOX "
            "credentials (never commit .env)."
        )

    client = PayPalClient(client_id, client_secret, mode="sandbox")
    try:
        # Step 1: token (proves credentials + connectivity; token never printed)
        client._get_token()
        print("[1/4] OAuth token: OK (sandbox)")

        if args.capture:
            # Step 3: capture
            request_id = str(uuid.uuid4())
            cap = client.capture_order(args.capture, paypal_request_id=request_id)
            # Capture response is the order; captures live under
            # purchase_units[].payments.captures[].
            capture_id, cap_status = args.capture, cap.get("status")
            try:
                first = cap["purchase_units"][0]["payments"]["captures"][0]
                capture_id, cap_status = first["id"], first["status"]
            except (KeyError, IndexError, TypeError):
                pass
            print(f"[3/4] Captured order {args.capture}")
            print(f"      capture_id={capture_id} status={cap_status}")
            print(f"      PayPal-Request-Id={request_id}")
            return 0

        if args.refund:
            # Step 4: refund + verify
            request_id = str(uuid.uuid4())
            refund = client.refund_capture(
                args.refund, amount=args.amount, paypal_request_id=request_id
            )
            refund_id = refund.get("id")
            print(f"[4/4] Refund issued for capture {args.refund}")
            print(f"      refund_id={refund_id} status={refund.get('status')}")
            verified = client.get_refund(refund_id)
            print(f"      verified via get_refund: status={verified.get('status')}")
            print(f"      PayPal-Request-Id={request_id}")
            return 0

        # Step 2: create order
        order = client.create_order("1.00", description="Pulse AI spike test")
        order_id = order["id"]
        approve = next(
            (l["href"] for l in order.get("links", []) if l.get("rel") == "approve"),
            None,
        )
        print(f"[2/4] Order created: id={order_id} status={order.get('status')}")
        print(f"      Approve URL: {approve}")
        print()
        print(">>> HUMAN STEP: open the URL above, log in with your sandbox")
        print(">>> PERSONAL (buyer) test account, and approve the $1.00 payment.")
        print(">>> Then run: python scripts/spike_refund.py --capture " + order_id)
        return 0
    except PayPalError as exc:
        return fail(f"PayPal API error (HTTP {exc.http_status}): {exc.details}")
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
