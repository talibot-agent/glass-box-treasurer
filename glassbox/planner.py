"""Deterministic planner: proposes FUND / CONVERT / DEFER / ESCALATE per obligation, gates each proposal through
the policy engine, writes every decision to the audit log, and re-plans ONLY affected decisions on new information.

Affected-set rules (the Kit 1 requirement):
- deposit in currency X: cash-dependent items (not FUND, not policy escalations) whose obligation currency or
  conversion sell currency is X. FUND items cannot improve with more cash, so they are left untouched.
- forecast-confidence change: CONVERT items only (C3 is the only clause that reads confidence).
Items kept untouched keep their cash reservations; the subset is re-planned against what remains.
"""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping

from .audit import AuditLog
from .models import Action, Obligation, Proposal, Verdict
from .policy import Policy, evaluate, required_amount

D = Decimal
BIG = D("1e15")


@dataclass
class PlanItem:
    obligation_id: str
    action: Action
    proposal: Proposal
    verdict: Verdict
    clauses: list[str]
    reasons: list[str]
    consumption: dict[str, Decimal] = field(default_factory=dict)  # spendable used, per currency
    cash_dependent: bool = True

    def key(self):
        p = self.proposal
        return (self.action, self.verdict, p.sell_currency, p.buy_currency, p.buy_amount)


class Planner:
    def __init__(self, policy: Policy, obligations: Iterable[Obligation], rates_to_usd: Mapping[str, Decimal],
                 audit: AuditLog):
        self.policy, self.rates, self.audit = policy, dict(rates_to_usd), audit
        self.obs = {o.id: o for o in obligations}
        self.base = policy.raw.get("base_currency", "USD")
        self.floors = {k: D(str(v)) for k, v in policy.c("reserve_floor")["floors"].items()}
        self.never_defer = set(policy.c("never_defer")["categories"])
        self.current: dict[str, PlanItem] = {}
        self.balances: dict[str, Decimal] = {}
        self.confidence = 1.0

    # ---------- helpers ----------
    def _usd(self, amount: Decimal, ccy: str) -> Decimal:
        return amount if ccy == self.base else amount * self.rates[ccy]

    def _priority(self, ob: Obligation):
        return (0 if ob.category in self.never_defer else 1, ob.due_hours, ob.id)

    def _snapshot(self, balances) -> str:
        s = json.dumps({k: str(v) for k, v in sorted(balances.items())})
        return hashlib.sha256(s.encode()).hexdigest()[:12]

    def _evidence(self, ob, snap, extra=()):
        return (f"balance_snapshot:{snap}", f"obligation:{ob.id}", f"forecast_confidence:{self.confidence:.2f}", *extra)

    def _spendable(self, balances, exclude: set[str]) -> dict[str, Decimal]:
        sp = {c: max(D(0), b - self.floors.get(c, D(0))) for c, b in balances.items()}
        for oid, it in self.current.items():
            if oid in exclude:
                continue
            for c, amt in it.consumption.items():
                sp[c] = sp.get(c, D(0)) - amt
        return sp

    def _gate(self, proposal, ob, balances_view):
        return evaluate(proposal, ob, self.policy, balances_view, self.rates, self.confidence)

    def _view(self, sp):  # real-balance view that the policy engine expects (spendable + floor)
        return {c: v + self.floors.get(c, D(0)) for c, v in sp.items()}

    # ---------- core ----------
    def _plan_subset(self, ids: list[str], balances) -> dict[str, PlanItem]:
        snap = self._snapshot(balances)
        sp = self._spendable(balances, exclude=set(ids))
        out: dict[str, PlanItem] = {}
        obs = sorted((self.obs[i] for i in ids), key=self._priority)

        # 1. policy escalations, independent of cash (new beneficiary, payout cap)
        live = []
        for ob in obs:
            probe = Proposal(ob.id, Action.FUND, evidence=self._evidence(ob, snap))
            d = self._gate(probe, ob, {ob.currency: BIG})
            if d.verdict == Verdict.NEEDS_HUMAN:
                out[ob.id] = PlanItem(ob.id, Action.ESCALATE, Proposal(ob.id, Action.ESCALATE, evidence=probe.evidence),
                                      Verdict.NEEDS_HUMAN, d.clauses, d.reasons, cash_dependent=False)
            else:
                live.append(ob)

        # 2. shortfall check in base currency; defer cheapest deferrable until covered
        def base_need(items):
            own = dict(sp); total = D(0)
            for ob in items:
                need = required_amount(ob, self.policy)
                if ob.currency == self.base:
                    total += need
                else:
                    use = min(own.get(ob.currency, D(0)), need); own[ob.currency] = own.get(ob.currency, D(0)) - use
                    total += self._usd(need - use, ob.currency)
            return total
        deferred = set()
        deferrable = sorted((o for o in live if o.category not in self.never_defer),
                            key=lambda o: (self._usd(o.amount, o.currency), o.id))
        while base_need([o for o in live if o.id not in deferred]) > sp.get(self.base, D(0)) and deferrable:
            deferred.add(deferrable.pop(0).id)

        # 3. greedy allocation in priority order
        for ob in live:
            ev = self._evidence(ob, snap)
            if ob.id in deferred:
                p = Proposal(ob.id, Action.DEFER, evidence=ev + ("reason:shortfall_cheapest_deferrable",))
                d = self._gate(p, ob, self._view(sp))
                out[ob.id] = PlanItem(ob.id, Action.DEFER, p, d.verdict, d.clauses, d.reasons)
                continue
            item = self._fund_or_convert(ob, sp, ev)
            for c, a in item.consumption.items():
                sp[c] = sp.get(c, D(0)) - a
            out[ob.id] = item
        return out

    def _fund_or_convert(self, ob, sp, ev) -> PlanItem:
        need = required_amount(ob, self.policy)
        own = sp.get(ob.currency, D(0))
        if own >= need:
            p = Proposal(ob.id, Action.FUND, evidence=ev)
            d = self._gate(p, ob, self._view(sp))
            if d.verdict != Verdict.REJECT:
                return PlanItem(ob.id, Action.FUND, p, d.verdict, d.clauses, d.reasons, {ob.currency: need})
        elif ob.currency != self.base:
            buy = (need - own).quantize(D("0.01"))
            cost = self._usd(buy, ob.currency)
            if sp.get(self.base, D(0)) >= cost:
                p = Proposal(ob.id, Action.CONVERT, self.base, ob.currency, buy,
                             evidence=ev + (f"fx_rate:{ob.currency}USD:{self.rates[ob.currency]}",))
                d = self._gate(p, ob, self._view(sp))
                if d.verdict != Verdict.REJECT:
                    return PlanItem(ob.id, Action.CONVERT, p, d.verdict, d.clauses, d.reasons,
                                    {self.base: cost, ob.currency: own})
        # cannot fund within policy
        if ob.category not in self.never_defer:
            p = Proposal(ob.id, Action.DEFER, evidence=ev + ("reason:insufficient_spendable",))
            return PlanItem(ob.id, Action.DEFER, p, Verdict.APPROVE, [], ["insufficient spendable cash"])
        p = Proposal(ob.id, Action.ESCALATE, evidence=ev + ("reason:never_defer_unfundable",))
        return PlanItem(ob.id, Action.ESCALATE, p, Verdict.NEEDS_HUMAN, [self.policy.c("never_defer")["id"]],
                        [f"{ob.category} cannot be deferred and cannot be funded within policy"])

    def _log(self, items: Mapping[str, PlanItem]):
        for it in items.values():
            p = it.proposal
            self.audit.append("decision", {"obligation": it.obligation_id, "action": it.action.value,
                                           "verdict": it.verdict.value, "clauses": it.clauses, "reasons": it.reasons,
                                           "sell": p.sell_currency, "buy": p.buy_currency, "buy_amount": p.buy_amount,
                                           "evidence": list(p.evidence)})

    # ---------- public API ----------
    def plan(self, balances: Mapping[str, Decimal], confidence: float) -> dict[str, PlanItem]:
        self.balances, self.confidence, self.current = dict(balances), confidence, {}
        self.audit.append("plan", {"balances": self.balances, "confidence": confidence, "rates": self.rates})
        self.current = self._plan_subset(sorted(self.obs), self.balances)
        self._log(self.current)
        return dict(self.current)

    def _recalc(self, ids: list[str], trigger: dict) -> dict:
        old = {i: self.current[i] for i in ids}
        new = self._plan_subset(ids, self.balances) if ids else {}
        changed = sorted(i for i in ids if old[i].key() != new[i].key())
        self.current.update(new)
        self.audit.append("recalc", {**trigger, "recalculated": ids, "changed": changed,
                                     "untouched": sorted(set(self.current) - set(ids))})
        self._log({i: new[i] for i in changed})
        return {"recalculated": ids, "changed": changed}

    def on_deposit(self, currency: str, new_balances: Mapping[str, Decimal]) -> dict:
        self.balances = dict(new_balances)
        ids = sorted(i for i, it in self.current.items() if it.cash_dependent and it.action != Action.FUND
                     and currency in {self.obs[i].currency, it.proposal.sell_currency})
        return self._recalc(ids, {"trigger": "deposit", "currency": currency})

    def update_confidence(self, confidence: float) -> dict:
        self.confidence = confidence
        ids = sorted(i for i, it in self.current.items() if it.action == Action.CONVERT)
        return self._recalc(ids, {"trigger": "confidence", "confidence": confidence})

    def projected_balances(self, balances: Mapping[str, Decimal]) -> dict[str, Decimal]:
        out = dict(balances)
        for it in self.current.values():
            if it.action == Action.FUND:
                c = self.obs[it.obligation_id].currency
                out[c] -= required_amount(self.obs[it.obligation_id], self.policy)
            elif it.action == Action.CONVERT:
                p = it.proposal; ob = self.obs[it.obligation_id]
                out[p.sell_currency] -= self._usd(p.buy_amount, p.buy_currency)
                out[p.buy_currency] += p.buy_amount - required_amount(ob, self.policy)
        return out
