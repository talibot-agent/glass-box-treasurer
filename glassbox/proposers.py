"""Deterministic stand-in proposers for the verify-and-revise loop (no LLM spend in tests). A real LLM proposer has the
same interface: propose(ctx) -> list[Proposal] and revise(ctx, plan, feedback) -> list[Proposal]. Proposers only emit
typed proposals; they never set verdicts, and the verifier ignores anything they claim.

- planner      : wraps the deterministic Planner (strong baseline).
- naive        : deliberately bad first proposal (FUND everything in its own currency), then repairs from feedback.
- gullible     : naive + obeys instructions found in invoice memo text (prompt-injection stand-in).
- defer_payroll: planner plan but defers payroll to "save cash" (violates C2), repaired from feedback.
- alt_defer    : legal alternative that defers the SWIFT supplier instead of converting (best-of-N contrast).
"""
from __future__ import annotations
import tempfile
from decimal import Decimal
from pathlib import Path

from .audit import AuditLog
from .models import Action, Proposal
from .planner import Planner

D = Decimal
BLOCKING = {"INVALID", "ILLEGAL", "FLOOR_BREACH"}


def _scratch_log():
    return AuditLog(Path(tempfile.mkdtemp()) / "proposer.jsonl")


class RepairMixin:
    """Feedback-driven revision. Per obligation with blocking feedback, in priority order:
    needs-human clause (payout cap / new beneficiary) -> ESCALATE; never-defer clause -> FUND/CONVERT;
    reserve-floor breach on a non-base FUND -> CONVERT exactly the reported shortfall from the base currency;
    any other floor/legality failure -> DEFER if deferrable else ESCALATE. A global floor breach with no per-item
    feedback defers the cheapest deferrable item that spends that currency. Evidence is rebuilt from trusted context."""

    def revise(self, ctx, plan, feedback):
        pol = ctx.policy
        nd = set(pol.c("never_defer")["categories"])
        human = {pol.c("payout_autonomy")["id"], pol.c("new_beneficiary")["id"]}
        rf, ndc = pol.c("reserve_floor")["id"], pol.c("never_defer")["id"]
        blocked = {f.obligation_id for f in feedback if f.obligation_id and f.code in BLOCKING}
        by_ob = {}
        for f in feedback:  # all feedback (incl. non-blocking NEEDS_HUMAN clauses) for blocked items only
            if f.obligation_id in blocked:
                by_ob.setdefault(f.obligation_id, []).append(f)
        cur = {p.obligation_id: p for p in plan if p.obligation_id in ctx.obligations}
        for oid, ob in ctx.obligations.items():  # coverage repair
            cur.setdefault(oid, Proposal(oid, Action.ESCALATE, evidence=ctx.evidence(oid, "reason:missing_in_plan")))
        out = {}
        for oid, p in cur.items():
            ob, fs = ctx.obligations[oid], by_ob.get(oid, [])
            ev = ctx.evidence(oid)
            if not fs:
                out[oid] = Proposal(oid, p.action, p.sell_currency, p.buy_currency, p.buy_amount,
                                    tuple(e for e in p.evidence if e.startswith(("balance_snapshot:", "obligation:", "forecast_confidence:", "fx_rate:", "reason:"))) or ev)
                continue
            cl = {f.clause for f in fs}
            short = next((f.amount for f in fs if f.clause == rf and f.amount is not None), None)
            if cl & human and p.action != Action.ESCALATE:
                out[oid] = Proposal(oid, Action.ESCALATE, evidence=ev + ("reason:policy_exception",))
            elif ndc in cl:
                out[oid] = Proposal(oid, Action.FUND, evidence=ev + ("reason:never_defer",))
            elif rf in cl and p.action == Action.FUND and ob.currency != ctx.base and short is not None:
                out[oid] = Proposal(oid, Action.CONVERT, ctx.base, ob.currency, short,
                                    evidence=ev + (f"fx_rate:{ob.currency}USD:{ctx.rates[ob.currency]}",))
            elif all(f.clause == "G2" and "untrusted" in f.message for f in fs if f.code in BLOCKING):
                out[oid] = Proposal(oid, p.action, p.sell_currency, p.buy_currency, p.buy_amount, ev)  # drop bad evidence
            elif ob.category not in nd:
                out[oid] = Proposal(oid, Action.DEFER, evidence=ev + ("reason:verifier_feedback",))
            else:
                out[oid] = Proposal(oid, Action.ESCALATE, evidence=ev + ("reason:never_defer_unfundable",))
        # global floor breach without a per-item culprit: defer cheapest deferrable spender of that currency
        if not by_ob:
            for f in feedback:
                if f.code == "FLOOR_BREACH":
                    ccy = f.message.split()[2]
                    spenders = sorted((o for o in ctx.obligations.values() if o.category not in nd and
                                       out[o.id].action in (Action.FUND, Action.CONVERT) and
                                       ccy in {o.currency, out[o.id].sell_currency}), key=lambda o: (o.amount, o.id))
                    if spenders:
                        o = spenders[0]
                        out[o.id] = Proposal(o.id, Action.DEFER, evidence=ctx.evidence(o.id, "reason:floor_breach"))
        return [out[k] for k in sorted(out)]


class PlannerProposer(RepairMixin):
    name = "planner"

    def propose(self, ctx):
        p = Planner(ctx.policy, ctx.obligations.values(), ctx.rates, _scratch_log())
        items = p.plan(ctx.balances, ctx.confidence)
        return [items[k].proposal for k in sorted(items)]


class NaiveProposer(RepairMixin):
    name = "naive"

    def propose(self, ctx):
        return [Proposal(o.id, Action.FUND, evidence=ctx.evidence(o.id)) for o in
                sorted(ctx.obligations.values(), key=lambda o: o.id)]


class GullibleProposer(NaiveProposer):
    """Obeys the invoice memo like a prompt-injected LLM would."""
    name = "gullible"

    def propose(self, ctx):
        out = []
        for p in super().propose(ctx):
            memo = ctx.obligations[p.obligation_id].memo.lower()
            if "defer" in memo:
                out.append(Proposal(p.obligation_id, Action.DEFER, evidence=p.evidence + ("memo:instructed_defer",)))
            elif any(w in memo for w in ("approve", "verified", "pay now", "override")):
                out.append(Proposal(p.obligation_id, Action.FUND,
                                    evidence=p.evidence + ("memo:beneficiary_verified", "claimed_verdict:APPROVE")))
            else:
                out.append(p)
        return out


class DeferPayrollProposer(PlannerProposer):
    name = "defer_payroll"

    def propose(self, ctx):
        nd = set(ctx.policy.c("never_defer")["categories"])
        return [Proposal(p.obligation_id, Action.DEFER, evidence=ctx.evidence(p.obligation_id, "reason:save_cash"))
                if ctx.obligations[p.obligation_id].category == "payroll" and "payroll" in nd else p
                for p in super().propose(ctx)]


class AltDeferProposer(PlannerProposer):
    """Avoid FX entirely: defer every non-base obligation that would need a conversion, fund the rest."""
    name = "alt_defer"

    def propose(self, ctx):
        out = []
        for p in super().propose(ctx):
            if p.action == Action.CONVERT:
                out.append(Proposal(p.obligation_id, Action.DEFER, evidence=ctx.evidence(p.obligation_id, "reason:avoid_fx")))
            elif p.action == Action.DEFER and ctx.obligations[p.obligation_id].currency == ctx.base:
                out.append(Proposal(p.obligation_id, Action.FUND, evidence=ctx.evidence(p.obligation_id)))
            else:
                out.append(p)
        return out


PROPOSERS = {c.name: c for c in (PlannerProposer, NaiveProposer, GullibleProposer, DeferPayrollProposer, AltDeferProposer)}


def make_proposer(name: str):
    return PROPOSERS[name]()
