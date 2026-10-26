# Glass Box Treasurer
Adaptive Treasury Controller (Airwallex Agentic Banking Hackathon, Starter Kit 01). The agent proposes FUND / CONVERT / DEFER / ESCALATE; a **deterministic policy engine** approves; every decision cites evidence and policy clauses; a **hash-chained audit log** makes it replayable.
Built with AI assistance (Tali Builder, a Grok Bot agent) for Mykel Nelson. Sandbox only, no real money.

## Layout
- `policies/default.yaml`: plain-language clauses C1–C6 (reserve floor, never-defer, confidence-tied FX autonomy, payout cap, new beneficiary, SWIFT fee)
- `glassbox/policy.py`: rules engine → APPROVE / NEEDS_HUMAN / REJECT with clause citations (REJECT dominates)
- `glassbox/audit.py`: append-only SHA-256 chained JSONL + `verify()`
- `glassbox/airwallex.py`: `AirwallexClient` protocol + `FakeAirwallex` (idempotent request_id, single-use quotes). Real HTTP client comes after sandbox signup.
- `tests/`: pytest (22 passing)

## Setup
```sh
pip install pyyaml pytest
python3 -m pytest -q
```
Sandbox keys (later): `AWX_CLIENT_ID`, `AWX_API_KEY` from env; never commit. Base URL `https://api-demo.airwallex.com` (`/api/v1/authentication/login`).

## Next
Real HTTP client + smoke (login, balances, global account, simulated deposit) → seed 5 obligations/3 currencies → planner + partial recalculation on deposit → executors → web UI/approval card → video.
