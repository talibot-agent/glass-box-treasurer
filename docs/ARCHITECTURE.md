# Glass Box Treasurer: Architecture
AI-assisted (Tali Builder, a Grok Bot agent). Airwallex Agentic Banking Hackathon, Starter Kit 01 (Adaptive Treasury Controller).

## Principle
Code decides, the model explains. Amounts, limits, reserves, idempotency and state transitions live in code
(builder-guide rule). An LLM may later rank or explain via typed tools, but every money-moving proposal must pass
three gates: **policy** (deterministic), **evidence** (cited inputs), **audit** (hash-chained, replayable).

```
 balances/rates ──► Planner ──► Proposal ──► Policy engine ──► Verdict ──► Audit log ──► Executor (next)
 (AirwallexClient)   │ priority, shortfall,    (C1–C6 YAML)    APPROVE /     SHA-256       FX conversion,
                     │ min conversion                          NEEDS_HUMAN / chain         transfer (idempotent
                     └─ incremental re-plan on deposit/conf    REJECT                      request_id)
```

## Modules
| Module | Role |
|---|---|
| `glassbox/models.py` | `Obligation`, `Proposal`, `Decision`, `Action` (FUND/CONVERT/DEFER/ESCALATE), `Verdict` |
| `policies/default.yaml` | Plain-language clauses with ids: C1 reserve floors, C2 never-defer (payroll, shipping), C3 FX autonomy by forecast confidence (≥0.8: 5k USD, ≥0.5: 1k, else 0), C4 payout cap 10k USD, C5 new beneficiary → human, C6 SWIFT fee EUR 12.85 |
| `glassbox/policy.py` | `evaluate()` → verdict + cited clauses. Strictest verdict wins (REJECT > NEEDS_HUMAN > APPROVE). Missing evidence → REJECT (G2) |
| `glassbox/planner.py` | Deterministic plan + incremental re-plan (below) |
| `glassbox/audit.py` | Append-only JSONL; each entry `hash = sha256(canonical(seq, kind, payload, prev))`; `verify()` detects tampering |
| `glassbox/airwallex.py` | `AirwallexClient` protocol + `FakeAirwallex` (idempotent `request_id`, single-use quotes, insufficient funds) |
| `glassbox/http_client.py` | `AirwallexHTTP`: real sandbox client, stdlib, injectable transport |
| `glassbox/seed.py` | Kit 1 scenario: 5 obligations, USD/EUR/GBP, ≤72h |
| `scripts/smoke.py`, `scripts/demo_offline.py` | Live auth/balances check; offline end-to-end story |

## Planner algorithm
1. **Policy escalations first**: probe each obligation with a FUND proposal against unlimited cash; anything
   needing a human regardless of cash (C4 cap, C5 new beneficiary) becomes ESCALATE and is not cash-dependent.
2. **Shortfall**: spendable = balance − floor per currency. Non-base obligations use own-currency spendable first,
   the rest is converted from USD. While the base-currency need exceeds spendable, defer the cheapest deferrable
   obligation (not in C2).
3. **Greedy allocation** in priority order (never-defer categories first, then due time): FUND if own currency
   covers it (SWIFT fee included), else CONVERT the **minimum** (`need − own spendable`) from USD, else DEFER, or
   ESCALATE if never-defer. Each proposal is gated by the policy engine; a REJECT falls through to the next option.
   A CONVERT that comes back NEEDS_HUMAN (C3) stays a CONVERT and waits for bound approval.
4. **Incremental re-plan**: untouched items keep their cash reservations; only the affected subset is re-planned
   against what remains.
   - *Deposit in X*: cash-dependent items that are not FUND and whose obligation or sell currency is X.
   - *Confidence change*: CONVERT items only (C3 is the only clause reading confidence).
   The audit `recalc` entry records `recalculated`, `changed` and `untouched`; changed decisions are re-logged.

Kit 1 run (`scripts/demo_offline.py`): SHIP/PAY FUND, SUPP CONVERT 3,212.85 EUR (incl. SWIFT fee), SAAS DEFER,
NEWV ESCALATE (C4, C5). Confidence 0.55 → SUPP needs a human (C3). USD 5,000 deposit → re-plans only SUPP+SAAS;
SAAS becomes FUND. Reserve floors hold; the audit chain verifies.

## Airwallex integration (verified against the builder guide, airwallexdev.com, 2026-10-26)
- Base `https://api.sandbox.airwallex.com/api/v1` (default; `api-demo` also authenticates). Login with headers
  `x-client-id`/`x-api-key`, no body → 30-min bearer (cached 25 min, one re-login on 401).
- Amounts are major units. `request_id` is a UUID, reused only for retries (`duplicate_request_id` otherwise).
- FX: no `x-api-version`. A rate check reads `rate`; a quote reads `client_rate`. Conversion body: `request_id`, `buy_currency`,
  `sell_currency`, `buy_amount`, optional `quote_id`; settles immediately (SETTLED). Insufficient funds code: `insufficient_funds`.
- Transfers: `transfer_currency`, `transfer_amount`, `transfer_method` LOCAL|SWIFT, `request_id` 10–50 chars;
  failure is CANCELLED + `failure_type`; code `balance_insufficient`.
- Deposit simulation: `global_account_id`, `amount`, `payer_name`; **no currency field** (it lands in the GA's
  currency); response PENDING but the balance posts immediately.
- Rate limit 10 req/s per endpoint, 20 global.

## Beneficiaries, executor, approvals
- `glassbox/beneficiaries.py` builds US/GB/DE payloads per corridor rules (aba + "Checking"; bank_name + lowercase
  sort_code; IBAN with local mod-97 check + swift_code). Flow: `/beneficiaries/schema` → `/beneficiaries/validate` → `/beneficiaries/create`.
- `glassbox/approvals.py`: a `Binding` = (obligation, action, amount to 2dp, currency, counterparty, sha256 of evidence).
  Approvals are requested → approved → consumed once; any field change gives a different digest, so no reuse.
- `glassbox/executor.py`: executes only CONVERT (fresh quote, then conversion) and FUND (transfer with the obligation's
  LOCAL/SWIFT method). NEEDS_HUMAN requires consuming the matching approval. `request_id = uuid5(action:obligation:binding)`
  is stable across retries; a persistent ledger is the duplicate lock (DONE ops are never resent, even after restart).

## Live sandbox verification (scripts/live_verify.py, 2026-10-26 ~7:40 PM ET, sandbox funds only)
| Step | Result |
|---|---|
| Conversion buy 10 EUR from USD (quote + conversion body) | SETTLED, sell 11.24 USD, `client_rate` 1.124136 |
| Re-run same conversion | ALREADY_DONE (duplicate lock, no API call) |
| Deposit 10 USD (`global_account_id`, `amount`, `payer_name`) | accepted, PENDING, posted (USD net −1.24 = −11.24 + 10) |
| Beneficiary schema DE/EUR/SWIFT; validate + create US LOCAL and DE SWIFT | OK |
| Transfer 10 EUR SWIFT to the DE beneficiary | SCHEDULED, **fee 13.43 EUR** (payer pays 23.43) |

Findings: (1) the live SWIFT fee was **EUR 13.43, not the guide's 12.85**. The fee stays in policy config (C6), and
the executor should reconcile against `fee_amount` (TODO: set C6 from a probe, or refuse if the fee exceeds C6).
(2) The quote's `client_rate` is **sell per buy** (USD per EUR); the fake uses buy per sell. Normalise before using
live rates in the planner. (3) The schema response lists `payment_methods`, but validate accepts `transfer_methods`
as the guide says. (4) The Global Account still reports PROCESSING, yet deposits post.

## Fees, rates, tracking, webhooks
- **Fees** (`glassbox/fees.py`): policy C6 reserves the observed live SWIFT fee (EUR 13.43) plus a 1.00 buffer = 14.43 per
  payout. After each transfer the executor reads `fee_amount`/`fee_currency`. FeeBook keeps the highest fee seen and lifts
  the policy fee (never lowers it). A fee above the policy's base is audited as `fee_mismatch`.
- **Rates** (`glassbox/rates.py`): `rate`/`client_rate` follow `currency_pair` (EURUSD = USD per EUR) whichever side you
  sell. Verified live: a USD→EUR and an EUR→USD check both return EURUSD. `rate_to_usd()` inverts USDxxx pairs.
  `quote_cost()` prefers the reported sell amount and refuses to guess without a pair. `live_rates_to_usd()` feeds the planner.
- **Tracking** (`glassbox/tracking.py`): polls `GET /transfers/{id}` with backoff until PAID or CANCELLED. SENT is in-flight.
  CANCELLED is reported as FAILED with `failure_type`/`failure_reason`. There is no FAILED status. A poll budget that runs out
  returns `final=False` (unknown, not failed). Each status change is audited.
- **Webhooks** (`glassbox/webhook.py`): `x-signature` = hex HMAC-SHA256(secret, `x-timestamp` + raw body). Verified on raw
  bytes with a constant-time compare and a 5-minute timestamp tolerance. Event ids are deduped and persisted; it returns 200 fast.
  `serve()` is a stdlib HTTP wrapper.

Live check #2 (`scripts/live_verify2.py`, 2026-10-26 ~7:29 PM ET, sandbox funds): live rates EUR 1.123242 and GBP 1.341317 USD
fed into the planner (same Kit 1 decisions; SUPP buys 3,214.43 EUR). A 10 EUR quote predicted 11.24 USD and the conversion
settled at 11.24. The SWIFT transfer was simulated SENT→PAID and tracked to PAID; fee 13.43 is within policy. A local
signed webhook returned valid 200, replay 200 (delivered once), tampered 401. The audit chain is valid.

## Not yet built
A registered Airwallex webhook subscription (needs a public HTTPS URL), routing webhook events into the tracker, a live CANCELLED-path test, the web UI,
an LLM explanation layer, and an optional Metal testnet hash anchor.
