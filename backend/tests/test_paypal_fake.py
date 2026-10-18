"""Tests for FakePayPalClient: idempotency, partial refunds, disputes."""

import pytest

from app.fake_paypal import FakePayPalClient
from app.gate import authorize, GateRefused
from app.paypal_client import PayPalError


def _completed_capture(client, value="25.00"):
    order = client.create_order(value)
    client.approve_order(order["id"])
    return client.capture_order(order["id"], paypal_request_id="key-cap-1")


# ------------------------------------------------------------ idempotency

def test_refund_idempotent_same_key_same_refund():
    client = FakePayPalClient()
    cap = _completed_capture(client)
    r1 = client.refund_capture(cap["id"], paypal_request_id="idem-1")
    r2 = client.refund_capture(cap["id"], paypal_request_id="idem-1")
    assert r1["id"] == r2["id"]
    assert len(client.refunds) == 1, "exactly one refund must be stored"


def test_refund_different_keys_create_separate_partial_refunds():
    client = FakePayPalClient()
    cap = _completed_capture(client, value="25.00")
    r1 = client.refund_capture(cap["id"], amount="10.00", paypal_request_id="k1")
    r2 = client.refund_capture(cap["id"], amount="10.00", paypal_request_id="k2")
    assert r1["id"] != r2["id"]
    assert len(client.refunds) == 2
    assert client.get_capture(cap["id"])["refunded_amount"]["value"] == "20.00"


def test_full_refund_marks_capture_refunded():
    client = FakePayPalClient()
    cap = _completed_capture(client, value="25.00")
    client.refund_capture(cap["id"], paypal_request_id="k1")
    assert client.get_capture(cap["id"])["status"] == "REFUNDED"


# ------------------------------------------------------------ gate interplay

def test_second_full_refund_refused_by_gate_after_refunded():
    client = FakePayPalClient()
    cap = _completed_capture(client, value="25.00")
    client.refund_capture(cap["id"], paypal_request_id="k1")
    live = client.get_capture(cap["id"])
    assert live["status"] == "REFUNDED"
    # Gate validates against the live capture state: a REFUNDED capture
    # is not COMPLETED, so any further refund is refused.
    with pytest.raises(GateRefused, match="REFUNDED"):
        authorize("refund_capture", {"capture": live, "amount": None})


def test_partial_then_over_refund_refused_by_paypal():
    client = FakePayPalClient()
    cap = _completed_capture(client, value="25.00")
    client.refund_capture(cap["id"], amount="20.00", paypal_request_id="k1")
    # The gate allows 5.00 or less against the capture total, but PayPal
    # itself refuses anything beyond the remaining 5.00.
    with pytest.raises(PayPalError):
        client.refund_capture(cap["id"], amount="25.00", paypal_request_id="k2")


def test_pending_capture_refund_refused_by_gate_and_fake():
    client = FakePayPalClient(capture_outcome="PENDING")
    order = client.create_order("25.00")
    client.approve_order(order["id"])
    cap = client.capture_order(order["id"], paypal_request_id="k1")
    assert cap["status"] == "PENDING"
    with pytest.raises(GateRefused):
        authorize("refund_capture", {"capture": cap, "amount": None})
    with pytest.raises(PayPalError):
        client.refund_capture(cap["id"], paypal_request_id="k2")


def test_denied_capture_outcome():
    client = FakePayPalClient(capture_outcome="DENIED")
    order = client.create_order("25.00")
    client.approve_order(order["id"])
    cap = client.capture_order(order["id"], paypal_request_id="k1")
    assert cap["status"] == "DENIED"
    with pytest.raises(GateRefused):
        authorize("refund_capture", {"capture": cap, "amount": None})


# ---------------------------------------------------------------- disputes

def test_dispute_provide_evidence_flow():
    client = FakePayPalClient()
    items = client.list_disputes()["items"]
    assert len(items) == 1
    dispute_id = items[0]["dispute_id"]
    detail = client.get_dispute(dispute_id)
    assert detail["status"] == "WAITING_FOR_SELLER_RESPONSE"
    # Gate allows it in this state.
    authorize("provide_evidence", {"dispute": detail})
    res = client.provide_evidence(dispute_id, {"evidence_type": "PROOF_OF_FULFILLMENT"})
    assert res["status"] == "UNDER_REVIEW"
    # A second evidence submission is now refused by the fake (state moved on).
    with pytest.raises(PayPalError):
        client.provide_evidence(dispute_id, {"evidence_type": "X"})


def test_dispute_accept_claim_flow():
    client = FakePayPalClient()
    dispute_id = client.list_disputes()["items"][0]["dispute_id"]
    authorize("accept_claim", {"dispute": client.get_dispute(dispute_id)})
    res = client.accept_claim(dispute_id, note="Accepting claim.")
    assert res["status"] == "RESOLVED"
    with pytest.raises(GateRefused):
        authorize("accept_claim", {"dispute": client.get_dispute(dispute_id)})
