#!/usr/bin/env python3
"""Real-sandbox dispute spike for PayPal Pulse AI.

This script does NOT create disputes automatically (PayPal has no
"create dispute" API for merchants; disputes are opened by the BUYER).
It:
  1. Lists disputes on the sandbox merchant account (count + ids + statuses).
  2. --dispute-id ID : prints status, reason, and response deadline.
  3. Prints numbered manual instructions for creating a sandbox dispute,
     so the Dispute Desk hero module can be verified by a human.

Exit code 0 on success, non-zero on failure.
Requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in the environment.

Usage:
    python scripts/spike_dispute.py
    python scripts/spike_dispute.py --dispute-id PP-D-12345
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paypal_client import PayPalClient, PayPalError  # noqa: E402

MANUAL_STEPS = """
MANUAL: how to create a sandbox dispute (needed to verify the Dispute Desk)
  1. In the sandbox, complete a real captured payment first:
       python scripts/spike_refund.py            # create + approve a $1 order
       python scripts/spike_refund.py --capture ORDER_ID
  2. Log in to https://www.sandbox.paypal.com with your sandbox PERSONAL
     (buyer) test account.
  3. Open the Resolution Center and file a dispute against that payment
     (e.g. "Item not received").
  4. Re-run this script: the dispute should appear in the merchant's
     list below, with status WAITING_FOR_SELLER_RESPONSE.
  5. Then verify provide-evidence / accept-claim against that dispute id.
     (Sandbox dispute lifecycles differ from live -- treat timings as
     sandbox-only behavior.)
"""


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispute-id", metavar="ID",
                        help="Show details for one dispute.")
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
        client._get_token()
        print("[1/3] OAuth token: OK (sandbox)")

        if args.dispute_id:
            d = client.get_dispute(args.dispute_id)
            print(f"[2/3] Dispute {args.dispute_id}")
            print(f"      status={d.get('status')}")
            print(f"      reason={d.get('reason')}")
            print(f"      response_due_date={d.get('response_due_date')}")
            msgs = d.get("messages", [])
            print(f"      messages={len(msgs)}")
            for m in msgs[:3]:
                who = m.get("posted_by", "?")
                content = str(m.get("content", ""))[:120]
                print(f"        [{who}] {content}")
            return 0

        data = client.list_disputes()
        items = data.get("items", [])
        print(f"[2/3] Disputes found: {len(items)}")
        for d in items:
            print(f"      id={d.get('dispute_id')} status={d.get('status')} "
                  f"reason={d.get('reason')}")
        if not items:
            print("      (none -- create one manually to verify the Dispute Desk)")
        print(MANUAL_STEPS)
        return 0
    except PayPalError as exc:
        return fail(f"PayPal API error (HTTP {exc.http_status}): {exc.details}")
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
