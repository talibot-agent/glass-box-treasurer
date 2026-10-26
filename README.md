# Glass Box Treasurer
Adaptive Treasury Controller (Airwallex Agentic Banking Hackathon, Starter Kit 01). The agent proposes FUND / CONVERT / DEFER / ESCALATE; a **deterministic policy engine** approves; every decision cites evidence and policy clauses; a **hash-chained audit log** makes it replayable.
> **AI authorship disclosure:** this code, its tests and its docs were written by an AI agent (Tali Builder, a Grok Bot
> agent) under the direction and approval of Mykel Nelson. Built for the Airwallex Agentic Banking hackathon. Sandbox only, no real money.

## Layout
- `policies/default.yaml`: plain-language clauses C1–C6 (reserve floor, never-defer, confidence-tied FX autonomy, payout cap, new beneficiary, SWIFT fee)
- `glassbox/policy.py`: rules engine → APPROVE / NEEDS_HUMAN / REJECT with clause citations (REJECT dominates)
- `glassbox/audit.py`: append-only SHA-256 chained JSONL + `verify()`
- `glassbox/airwallex.py`: `AirwallexClient` protocol + `FakeAirwallex` (idempotent request_id, single-use quotes). Real HTTP client comes after sandbox signup.
- `glassbox/http_client.py`: real sandbox client `AirwallexHTTP` (stdlib; token cache ~25 min, one re-login on 401, injectable transport, repr hides secrets). Creds from env `AWX_CLIENT_ID`/`AWX_API_KEY` or `_secrets/airwallex_sandbox.txt`
- `glassbox/seed.py`: Kit 1 scenario, 5 obligations (USD/EUR/GBP, ≤72h): shipping, SWIFT EUR supplier, SaaS, new-vendor GBP, payroll
- `scripts/smoke.py`: login + non-zero balances + global accounts (`--fund USD 25000` for a simulated deposit)
- `glassbox/planner.py`: deterministic planner + incremental re-plan on deposit / confidence change, audited
- `scripts/demo_offline.py`: offline Kit 1 story
- `glassbox/beneficiaries.py`, `approvals.py`, `executor.py`: corridor payloads, bound single-use approvals, idempotent executor
- `glassbox/fees.py`, `rates.py`, `tracking.py`, `webhook.py`: fee reconciliation, pair-direction rates, transfer tracking to terminal, signed webhooks
- `scripts/live_verify2.py`: live rates, quote-vs-settlement check, SENT→PAID tracking, local webhook round trip
- `scripts/live_verify.py`: small-amount live sandbox check (writes gitignored `out/`)
- `glassbox/ui.py`: local dashboard (plan, decisions with cited clauses, approvals with an approve button, transfers, audit log). Run `python3 scripts/ui_demo.py` and open http://127.0.0.1:8800
- `scripts/webhook_server.py`: webhook receiver that feeds `TransferState`
- `tests/`: pytest (90 passing)

See `docs/ARCHITECTURE.md` for the design.

## Setup
```sh
pip install pyyaml pytest
python3 -m pytest -q
```
Smoke: `python3 scripts/smoke.py`. Base URL defaults to `https://api.sandbox.airwallex.com` (official); `api-demo.airwallex.com` also authenticates (both tested 2026-10-26). Override with `AWX_BASE_URL`.
Note: a new sandbox comes pre-funded with 10M each of USD/EUR/GBP/CNY, so the demo uses scenario balances from `seed.py` rather than the raw account balances.

## Next
Beneficiaries + executors (quote→conversion, transfer) with duplicate lock → approval binding → web UI/approval card → video.
