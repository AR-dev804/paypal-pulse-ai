"""Safe Action Gate: the AI proposes, this code disposes.

The gate NEVER calls PayPal itself and NEVER trusts AI-generated
instructions as authorization. It validates three things, in order:

1. The action is on the allowlist.
2. The action ORIGINATED from an approved source. Anything arriving
   solely as untrusted buyer-provided text (prompt injection) is
   refused before any state is even checked — a poisoned message can
   never authorize an action, even when the capture state would allow
   the same action from a human.
3. The action is valid for the current, server-fetched state.

Anything else raises GateRefused with a human-readable reason. The demo
"attack" (a poisoned buyer message telling the agent to refund in full)
dies at check 2: the gate has no concept of obeying message text, only
of checking origin, states and amounts.
"""

from __future__ import annotations

from typing import Any, Optional

ALLOWLIST = {"refund_capture", "provide_evidence", "accept_claim"}

# Where an action request came from. Only "human" (the merchant approved
# it in the dashboard UI) may authorize real actions. "untrusted" marks
# anything originating solely from buyer-provided text — e.g. the
# simulated poisoned message in /api/demo/attack. The gate refuses
# untrusted-origin actions deterministically, regardless of state.
ORIGINS = {"human", "untrusted"}

# Dispute statuses where each action is legitimate (PayPal Disputes v1).
DISPUTE_EVIDENCE_STATUSES = {"WAITING_FOR_SELLER_RESPONSE"}
DISPUTE_CLAIM_STATUSES = {
    "OPEN",
    "WAITING_FOR_BUYER_RESPONSE",
    "WAITING_FOR_SELLER_RESPONSE",
    "UNDER_REVIEW",
}


class GateRefused(Exception):
    """Raised when the gate refuses an action. The .reason is user-facing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_refund(capture: dict, amount: Optional[str]) -> tuple[bool, str]:
    """Check a refund against the live capture state.

    Returns (True, "") when allowed, else (False, reason).
    """
    status = capture.get("status")
    if status != "COMPLETED":
        return False, (
            f"Refund refused: capture {capture.get('id')} is {status}; "
            "only COMPLETED captures can be refunded."
        )
    captured = capture.get("amount", {})
    try:
        captured_value = float(captured.get("value", "0"))
    except (TypeError, ValueError):
        return False, "Refund refused: capture amount unreadable."
    if amount is None:
        return True, ""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return False, f"Refund refused: amount {amount!r} is not a number."
    if value <= 0:
        return False, f"Refund refused: amount {value} must be positive."
    if value > captured_value + 1e-9:
        return (
            False,
            f"Refund refused: {value:.2f} exceeds captured {captured_value:.2f}.",
        )
    return True, ""


def validate_dispute_action(dispute: dict, action: str) -> tuple[bool, str]:
    """Check a dispute action against the live dispute state."""
    status = dispute.get("status")
    dispute_id = dispute.get("dispute_id", dispute.get("id", "?"))
    if action == "provide_evidence":
        if status in DISPUTE_EVIDENCE_STATUSES:
            return True, ""
        return False, (
            f"Evidence refused: dispute {dispute_id} is {status}; evidence is "
            "only accepted while WAITING_FOR_SELLER_RESPONSE."
        )
    if action == "accept_claim":
        if status in DISPUTE_CLAIM_STATUSES:
            return True, ""
        return False, (
            f"Accept-claim refused: dispute {dispute_id} is {status}."
        )
    return False, f"Unknown dispute action {action!r}."


def authorize(
    action_name: str, payload: dict[str, Any], origin: str = "human"
) -> dict[str, Any]:
    """Authorize an action. Raises GateRefused if not allowed.

    Expected payload shapes:
      refund_capture:   {"capture": <capture dict>, "amount": str|None}
      provide_evidence: {"dispute": <dispute dict>}
      accept_claim:     {"dispute": <dispute dict>}

    origin must be "human" (merchant approved the action in the
    dashboard UI). Anything originating solely from untrusted
    buyer-provided text must be passed as "untrusted" and is refused
    deterministically, before any state checks. The default is
    "human" for backward compatibility with the human-approved call
    sites.

    Returns a normalized authorization record on success.
    """
    if action_name not in ALLOWLIST:
        raise GateRefused(
            f"Action {action_name!r} is not allowlisted. "
            f"Allowed actions: {sorted(ALLOWLIST)}."
        )
    if origin not in ORIGINS:
        raise GateRefused(
            f"Unknown action origin {origin!r}. "
            f"Expected one of: {sorted(ORIGINS)}."
        )
    if origin == "untrusted":
        raise GateRefused(
            "Refused: this action originated solely from untrusted "
            "buyer-provided text. The Safe Action Gate only executes "
            "actions a human approves in the dashboard."
        )
    if action_name == "refund_capture":
        capture = payload.get("capture") or {}
        ok, reason = validate_refund(capture, payload.get("amount"))
        if not ok:
            raise GateRefused(reason)
    elif action_name in ("provide_evidence", "accept_claim"):
        dispute = payload.get("dispute") or {}
        ok, reason = validate_dispute_action(dispute, action_name)
        if not ok:
            raise GateRefused(reason)
    return {
        "action": action_name,
        "authorized": True,
        "origin": origin,
        "target": (
            (payload.get("capture") or {}).get("id")
            or (payload.get("dispute") or {}).get("dispute_id")
            or (payload.get("dispute") or {}).get("id")
        ),
    }
