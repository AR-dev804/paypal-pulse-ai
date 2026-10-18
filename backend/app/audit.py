"""Append-only audit log (SQLite).

Every meaningful action the system takes — investigations, authorizations,
refusals, PayPal executions, verifications — gets one row. Nothing is ever
updated or deleted; the log is the product's memory and the demo's proof.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT,
    summary TEXT NOT NULL,
    paypal_request_id TEXT,
    result TEXT NOT NULL
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def append(
    db_path: str,
    *,
    actor: str,
    action: str,
    summary: str,
    target_id: Optional[str] = None,
    paypal_request_id: Optional[str] = None,
    result: str = "ok",
) -> int:
    """Append one audit row. Returns the row id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO audit_log "
            "(ts, actor, action, target_id, summary, paypal_request_id, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor,
                action,
                target_id,
                summary,
                paypal_request_id,
                result,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_recent(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent rows, newest first."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, actor, action, target_id, summary, "
            "paypal_request_id, result FROM audit_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
