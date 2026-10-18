# PayPal Pulse AI

**The payment agent that can't be tricked.** A merchant-facing AI
payment-operations assistant built on the PayPal sandbox for the
[PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/).

The loop: **investigate a payment → AI diagnosis (cited evidence only) →
human approves → Safe Action Gate validates → PayPal API executes →
result is verified → audit timeline.** The AI proposes; the code disposes.

Sandbox only — live mode is refused at startup, and no real money moves.

## Status (honest, 2026-10-14)

Working and tested:

- PayPal REST client (sandbox only, `PayPal-Request-Id` idempotency, 15s
  timeout, token caching) + in-memory fake client with identical
  signatures for tests and offline dev
- SQLite payment store and append-only audit log
- Safe Action Gate: allowlisted actions only (`refund_capture`,
  `provide_evidence`, `accept_claim`); server re-checks capture state and
  amount before any money moves
- Gemini 2.5 Flash structured-output investigation (summary, what happened,
  what it means, recommended actions, confidence, cited API fields);
  deterministic rule-based fallback when no key is set — never invented
  IDs or amounts
- Dashboard (single-file HTML, self-contained CSS — no CDN dependency):
  payments table with status pills, investigation panel, human-approval
  refund modal, red BLOCKED adversarial demo, audit timeline, honest
  "Demo data — not live PayPal" banner
- Full API: health, payments, investigate, refund prepare/confirm,
  disputes, audit, demo seed, attack simulation
- 46/46 pytest tests passing

Done:

- Real-sandbox spike — VERIFIED 2026-10-16: token → order → buyer approval
  → capture → refund → read-back verification, all against the PayPal
  sandbox (fake client used only for demos since)

Pending:

- Dispute Desk hero module — gated on a verified real-sandbox dispute
  lifecycle; not claimed until proven. A real dispute was filed by the
  partner (ID private), but provide-evidence / accept-claim were not run
  against a live dispute, so the actions stay unclaimed.
- Render deployment, demo video, Devpost submission

## Structure

```
paypal-pulse-ai/
  backend/app/
    main.py          # FastAPI app: serves the dashboard at / + JSON API
    paypal_client.py # PayPal REST client (sandbox only, idempotent POSTs)
    fake_paypal.py   # in-memory fake with identical signatures
    store.py         # SQLite payment store
    gate.py          # Safe Action Gate: allowlist + state validation
    audit.py         # append-only SQLite audit log
    ai.py            # Gemini investigation + deterministic fallback
  backend/tests/     # pytest: gate, fake client, AI schema, API routes
  scripts/
    spike_refund.py  # real-sandbox: token -> order -> capture -> refund
    spike_dispute.py # real-sandbox: list disputes + manual creation guide
  frontend/index.html# dashboard (HTML + vanilla JS + self-contained CSS)
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your SANDBOX credentials; never commit .env
```

Without credentials the app runs against the fake PayPal client
(`FAKE_PAYPAL=true` in `.env.example`) — perfect for UI dev and tests.

## Run the dashboard

```bash
.venv/bin/python -m uvicorn backend.app.main:app --port 8123
# open http://127.0.0.1:8123/
```

Demo flow (all on the page):

1. **Seed demo data** — creates simulated completed / pending / denied
   payments (clearly labelled demo data)
2. Click a payment → **Investigate** — AI diagnosis with cited fields
3. **Refund** → review the approval modal → **Confirm refund** — the gate
   re-validates capture state and amount, then executes idempotently and
   verifies by reading the refund back
4. **Simulate attack** — a poisoned buyer message tries to force a refund;
   the gate returns a red **BLOCKED** decision and logs it

Refunding a PENDING payment is refused with HTTP 409 — the gate, not the
AI, decides.

## Spike scripts (real sandbox — needs .env)

```bash
# 1. Token check + create a $1.00 order (prints an approval URL)
python scripts/spike_refund.py
# 2. Approve it in the sandbox with your PERSONAL (buyer) test account, then:
python scripts/spike_refund.py --capture ORDER_ID
# 3. Full refund (or partial with --amount 0.50):
python scripts/spike_refund.py --refund CAPTURE_ID

# Disputes: list, inspect, and get manual creation instructions
python scripts/spike_dispute.py
python scripts/spike_dispute.py --dispute-id PP-D-XXXX
```

## Tests

```bash
.venv/bin/python -m pytest backend/tests -q
```

## API routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | dashboard |
| GET | `/health` | status, mode, fake/real client |
| GET | `/api/payments` | list payments |
| GET | `/api/payments/{id}` | payment detail |
| POST | `/api/investigate` | AI diagnosis (advisory only) |
| POST | `/api/actions/refund/prepare` | validate + stage a refund |
| POST | `/api/actions/refund/confirm` | human-approved, gate-checked refund |
| GET | `/api/disputes` | dispute list (real client only) |
| GET | `/api/audit` | audit timeline |
| POST | `/api/demo/seed` | seed simulated payments |
| POST | `/api/demo/attack` | adversarial prompt-injection demo |

## Design decisions

- **Backend is authoritative.** AI output is advisory; every money-moving
  action passes the deterministic Safe Action Gate on the server.
- **Buyer messages are untrusted data.** They are quoted to the AI as
  data, never as instructions; the attack demo proves the boundary.
- **Idempotency.** Every refund POST carries a `PayPal-Request-Id`; a
  retried confirm cannot double-refund.
- **Verify, don't trust.** After a refund the app reads the refund back
  from PayPal and records the verified state.
- **No silent failures.** Gemini parse failures fall back to the
  deterministic investigator; the UI shows loading/empty/error states.

## Honest limitations

- Sandbox only. Nothing here touches real money or a live PayPal account.
- Dispute actions are listed only where the sandbox spike has verified
  them; unverified actions are not exposed.
- The AI diagnosis cites supplied API fields — it cannot see anything the
  API didn't return.

## License

MIT — see LICENSE.
