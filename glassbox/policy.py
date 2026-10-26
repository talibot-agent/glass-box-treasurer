"""Deterministic policy engine. The LLM proposes; only this module approves."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import yaml

from .models import Action, Decision, Obligation, Proposal, Verdict

D = Decimal


@dataclass(frozen=True)
class Policy:
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        data = yaml.safe_load(Path(path).read_text())
        cls._validate(data)
        return cls(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        cls._validate(data)
        return cls(data)

    @staticmethod
    def _validate(data: dict) -> None:
        need = {"reserve_floor", "never_defer", "fx_autonomy", "payout_autonomy", "new_beneficiary", "swift_fee"}
        missing = need - set((data or {}).get("clauses", {}))
        if missing:
            raise ValueError(f"policy missing clauses: {sorted(missing)}")

    def c(self, name: str) -> dict:
        return self.raw["clauses"][name]

    def fx_limit_usd(self, confidence: float) -> Decimal:
        fx = self.c("fx_autonomy")
        bands, limits = fx["confidence_bands"], fx["limits_usd"]
        if confidence >= bands["high"]:
            return D(str(limits["high"]))
        if confidence >= bands["medium"]:
            return D(str(limits["medium"]))
        return D(str(limits["low"]))

    def swift_fee(self) -> tuple[str, Decimal]:
        f = self.c("swift_fee")["fee"]
        return f["currency"], D(str(f["amount"]))


def to_usd(amount: Decimal, currency: str, rates_to_usd: Mapping[str, Decimal]) -> Decimal:
    if currency == "USD":
        return amount
    if currency not in rates_to_usd:
        raise KeyError(f"no USD rate for {currency}")
    return amount * D(str(rates_to_usd[currency]))


def required_amount(ob: Obligation, policy: Policy) -> Decimal:
    """Amount needed in the obligation currency, including the SWIFT fee when it applies."""
    fee_ccy, fee = policy.swift_fee()
    if ob.method == "SWIFT" and fee_ccy == ob.currency:
        return ob.amount + fee
    return ob.amount


def evaluate(
    proposal: Proposal,
    obligation: Obligation,
    policy: Policy,
    balances: Mapping[str, Decimal],
    rates_to_usd: Mapping[str, Decimal],
    forecast_confidence: float,
) -> Decision:
    if proposal.obligation_id != obligation.id:
        raise ValueError("proposal/obligation mismatch")
    dec = Decision(proposal, Verdict.APPROVE)

    def flag(verdict: Verdict, clause: str, reason: str) -> None:
        order = [Verdict.APPROVE, Verdict.NEEDS_HUMAN, Verdict.REJECT]
        if order.index(verdict) > order.index(dec.verdict):
            dec.verdict = verdict
        dec.clauses.append(clause)
        dec.reasons.append(reason)

    if not proposal.evidence and proposal.action != Action.ESCALATE:
        flag(Verdict.REJECT, "G2", "no cited evidence")

    a = proposal.action
    if a == Action.ESCALATE:
        flag(Verdict.NEEDS_HUMAN, "ESCALATE", "agent escalated to a human")
        return dec

    if a == Action.DEFER:
        nd = policy.c("never_defer")
        if obligation.category in nd["categories"]:
            flag(Verdict.REJECT, nd["id"], f"{obligation.category} may never be deferred")
        return dec

    floors = {k: D(str(v)) for k, v in policy.c("reserve_floor")["floors"].items()}
    rf_id = policy.c("reserve_floor")["id"]
    need = required_amount(obligation, policy)

    if a == Action.CONVERT:
        if not (proposal.sell_currency and proposal.buy_currency and proposal.buy_amount):
            flag(Verdict.REJECT, "G1", "convert proposal missing legs")
            return dec
        if proposal.buy_currency != obligation.currency:
            flag(Verdict.REJECT, "G1", "buy currency does not match obligation")
        if proposal.buy_amount < need - balances.get(obligation.currency, D(0)):
            fee_note = " (incl. SWIFT fee)" if need != obligation.amount else ""
            flag(Verdict.REJECT, policy.c("swift_fee")["id"] if need != obligation.amount else "G1",
                 f"conversion leaves obligation short{fee_note}")
        buy_usd = to_usd(proposal.buy_amount, proposal.buy_currency, rates_to_usd)
        rate = D(str(rates_to_usd.get(proposal.sell_currency, 1))) if proposal.sell_currency != "USD" else D(1)
        sell_amount = buy_usd / rate
        after = balances.get(proposal.sell_currency, D(0)) - sell_amount
        if after < floors.get(proposal.sell_currency, D(0)):
            flag(Verdict.REJECT, rf_id, f"{proposal.sell_currency} would fall below reserve floor")
        limit = policy.fx_limit_usd(forecast_confidence)
        if buy_usd > limit:
            flag(Verdict.NEEDS_HUMAN, policy.c("fx_autonomy")["id"],
                 f"FX {buy_usd:.2f} USD exceeds autonomous limit {limit} at confidence {forecast_confidence:.2f}")
        return dec

    if a == Action.FUND:
        after = balances.get(obligation.currency, D(0)) - need
        if after < floors.get(obligation.currency, D(0)):
            flag(Verdict.REJECT, rf_id, f"{obligation.currency} would fall below reserve floor")
        usd = to_usd(need, obligation.currency, rates_to_usd)
        pa = policy.c("payout_autonomy")
        if usd > D(str(pa["max_usd"])):
            flag(Verdict.NEEDS_HUMAN, pa["id"], f"payout {usd:.2f} USD above autonomy cap")
        if not obligation.beneficiary_verified:
            flag(Verdict.NEEDS_HUMAN, policy.c("new_beneficiary")["id"], "new beneficiary")
        return dec

    raise ValueError(f"unknown action {a}")
