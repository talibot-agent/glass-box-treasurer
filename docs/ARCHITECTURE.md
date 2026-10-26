# Glass Box Treasurer
Adaptive Treasury Controller (Airwallex Agentic Banking Hackathon, Starter Kit 01). The agent proposes FUND / CONVERT / DEFER / ESCALATE; a **deterministic policy engine** approves; every decision cites evidence and policy clauses; a **hash-chained audit log** makes it replayable.
Built with AI assistance (Tali Builder, a Grok Bot agent) for Mykel Nelson. Sandbox only, no real money.

## Layout
- `policies/default.yaml`: plain-language clauses C1–C6 (reserve floor, never-defer, confidence-tied FX autonomy, payout cap, new beneficiary, SWIFT fee)
- `glassbox/policy.py`: rules engine → APPROVE / NEEDS_HUMAN / REJECT with clause citations (REJECT dominates)
- `glassbox/audit.py`: append-only SHA-256 chained JSONL + `verify()`
- `glassbox/airwallex.py`: `AirwallexClient` protocol + `FakeAirwallex` (idempotent request_id, single-use quotes). Real HTTP client comes after sandbox signup.
- `glassbox/http_client.py`: real sandbox client `AirwallexHTTP` (stdlib; token cache ~25 min, one re-login on 401, injectable transport, repr hides secrets). Creds from env `AWX_CLIENT_ID`/`AWX_API_KEY` or `_secrets/airwallex_sandbox.txt`
- `glassbox/seed.py`: Kit 1 scenario, 5 obligations (USD/EUR/GBP, ≤72h): shipping, SWIFT EUR supplier, SaaS, new-vendor GBP, payroll
- `scripts/smoke.py`: login + non-zero balances + global accounts (`--fund USD 25000` for a simulated deposit)
- `tests/`: pytest (31 passing)

## Setup
```sh
pip install pyyaml pytest
python3 -m pytest -q
```
Smoke: `python3 scripts/smoke.py`. Base URL defaults to `https://api-demo.airwallex.com`; `api.sandbox.airwallex.com` also authenticates (both tested 2026-10-26). Override with `AWX_BASE_URL`.
Note: a new sandbox comes pre-funded with 10M each of USD/EUR/GBP/CNY, so the demo uses scenario balances from `seed.py` rather than the raw account balances.

## Next
Planner + partial recalculation on deposit → executors → web UI/approval card → video.
