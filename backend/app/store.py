"""SQLite store for OUR payment records.

PayPal is the source of truth for money state; this store is the
dashboard's local index of payments we created/tracked. It never
overrides a live API response — routes re-fetch from PayPal before
any sensitive action.

Path is configurable via the PULSE_DB env var (default ./pulse.db).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

STATUSES = {"CREATED", "APPROVED", "COMPLETED", "PENDING", "DENIED", "REFUNDED"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paypal_order_id TEXT,
    capture_id TEXT,
    amount TEXT,
    currency TEXT,
    status TEXT NOT NULL,
    buyer_note TEXT,
    created_at TEXT NOT NULL,
    raw_json TEXT
);
"""


def db_path() -> str:
    """Resolve the store path from the environment (read at call time)."""
    return os.environ.get("PULSE_DB", "./pulse.db")


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def save_payment(
    *,
    paypal_order_id: Optional[str] = None,
    capture_id: Optional[str] = None,
    amount: Optional[str] = None,
    currency: str = "USD",
    status: str = "CREATED",
    buyer_note: Optional[str] = None,
    raw: Optional[dict] = None,
) -> int:
    """Insert one payment record. Returns the row id."""
    if status not in STATUSES:
        raise ValueError(f"Unknown payment status {status!r}.")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO payments (paypal_order_id, capture_id, amount, currency,"
            " status, buyer_note, created_at, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                paypal_order_id,
                capture_id,
                amount,
                currency,
                status,
                buyer_note,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(raw) if raw is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_payment(pid: int) -> Optional[dict[str, Any]]:
    """Fetch one payment by id, or None."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, paypal_order_id, capture_id, amount, currency, status,"
            " buyer_note, created_at, raw_json FROM payments WHERE id = ?",
            (pid,),
        ).fetchone()
        if row is None:
            return None
        payment = dict(row)
        if payment["raw_json"]:
            try:
                payment["raw_json"] = json.loads(payment["raw_json"])
            except (TypeError, ValueError):
                pass
        return payment
    finally:
        conn.close()


def list_payments() -> list[dict[str, Any]]:
    """List all payments, newest first."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, paypal_order_id, capture_id, amount, currency, status,"
            " buyer_note, created_at FROM payments ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_status(
    pid: int, status: str, capture_id: Optional[str] = None
) -> bool:
    """Update a payment's status (and capture id when known). Returns True if found."""
    if status not in STATUSES:
        raise ValueError(f"Unknown payment status {status!r}.")
    conn = _connect()
    try:
        if capture_id is not None:
            cur = conn.execute(
                "UPDATE payments SET status = ?, capture_id = ? WHERE id = ?",
                (status, capture_id, pid),
            )
        else:
            cur = conn.execute(
                "UPDATE payments SET status = ? WHERE id = ?",
                (status, pid),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
