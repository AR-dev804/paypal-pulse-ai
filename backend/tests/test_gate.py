"""Tests for the Safe Action Gate."""

import pytest

from app.gate import (
    ALLOWLIST,
    GateRefused,
    authorize,
    validate_dispute_action,
    validate_refund,
)


def _capture(status="COMPLETED", value="25.00"):
    return {
        "id": "CAPTURE-1",
        "status": status,
        "amount": {"currency_code": "USD", "value": value},
    }


def _dispute(status="WAITING_FOR_SELLER_RESPONSE"):
    return {"dispute_id": "DISPUTE-1", "status": status}


# ------------------------------------------------------------ allowlist

def test_non_allowlisted_action_refused():
    with pytest.raises(GateRefused, match="not allowlisted"):
        authorize("payout", {})


def test_allowlist_contents():
    assert ALLOWLIST == {"refund_capture", "provide_evidence", "accept_claim"}


# ---------------------------------------------------------------- refunds

def test_refund_completed_full_ok():
    ok, reason = validate_refund(_capture(), None)
    assert ok and reason == ""


def test_refund_partial_ok():
    ok, _ = validate_refund(_capture(value="25.00"), "10.00")
    assert ok


def test_refund_pending_capture_refused():
    ok, reason = validate_refund(_capture(status="PENDING"), None)
    assert not ok and "PENDING" in reason


def test_refund_denied_capture_refused():
    ok, _ = validate_refund(_capture(status="DENIED"), None)
    assert not ok


def test_refund_zero_amount_refused():
    ok, _ = validate_refund(_capture(), "0.00")
    assert not ok


def test_refund_negative_amount_refused():
    ok, _ = validate_refund(_capture(), "-5.00")
    assert not ok


def test_refund_exceeding_amount_refused():
    ok, reason = validate_refund(_capture(value="25.00"), "30.00")
    assert not ok and "exceeds" in reason


def test_refund_non_numeric_amount_refused():
    ok, _ = validate_refund(_capture(), "lots")
    assert not ok


def test_authorize_refund_success_shape():
    rec = authorize("refund_capture", {"capture": _capture(), "amount": None})
    assert rec["authorized"] is True
    assert rec["target"] == "CAPTURE-1"


def test_authorize_refund_bad_state_raises():
    with pytest.raises(GateRefused):
        authorize("refund_capture", {"capture": _capture(status="PENDING")})


# ---------------------------------------------------------------- disputes

def test_provide_evidence_ok_when_waiting_seller():
    ok, _ = validate_dispute_action(_dispute(), "provide_evidence")
    assert ok


def test_provide_evidence_refused_when_resolved():
    ok, reason = validate_dispute_action(_dispute(status="RESOLVED"), "provide_evidence")
    assert not ok and "RESOLVED" in reason


def test_accept_claim_ok_when_open():
    ok, _ = validate_dispute_action(_dispute(status="OPEN"), "accept_claim")
    assert ok


def test_accept_claim_refused_when_resolved():
    ok, _ = validate_dispute_action(_dispute(status="RESOLVED"), "accept_claim")
    assert not ok


def test_authorize_unknown_dispute_action_refused():
    with pytest.raises(GateRefused):
        authorize("provide_evidence", {"dispute": _dispute(status="RESOLVED")})


# ---------------------------------------------------------------- origin

def test_human_origin_explicit_ok():
    rec = authorize(
        "refund_capture", {"capture": _capture(), "amount": None}, origin="human"
    )
    assert rec["authorized"] is True and rec["origin"] == "human"


def test_unknown_origin_refused():
    with pytest.raises(GateRefused, match="Unknown action origin"):
        authorize(
            "refund_capture", {"capture": _capture(), "amount": None},
            origin="ai",
        )


def test_untrusted_origin_refused_on_refundable_capture():
    """A poisoned buyer message must never authorize a refund — even
    when the capture is COMPLETED and the same action from a human
    would be allowed."""
    with pytest.raises(GateRefused, match="untrusted"):
        authorize(
            "refund_capture", {"capture": _capture(), "amount": None},
            origin="untrusted",
        )


def test_untrusted_origin_refused_for_dispute_actions():
    with pytest.raises(GateRefused, match="untrusted"):
        authorize(
            "provide_evidence", {"dispute": _dispute()}, origin="untrusted"
        )
    with pytest.raises(GateRefused, match="untrusted"):
        authorize(
            "accept_claim",
            {"dispute": _dispute(status="OPEN")},
            origin="untrusted",
        )
