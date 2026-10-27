"""In-product verify-and-revise loop (ComPilot pattern, arXiv 2511.00592): the proposer emits a TYPED action plan, a
ground-truth verifier decides, structured feedback drives a revision, best-of-N verified plans are ranked by cost,
and only the winning verified plan executes.

  proposer.propose() -> plan (list[Proposal], one per obligation)
  stage 1  validity    : coverage (each obligation exactly once), known ids/currencies, amounts > 0, legs match,
                         evidence cites the obligation and only trusted sources (invoice memo text is never evidence)
  stage 2  legality    : the deterministic policy engine (policy.evaluate) on running balances, in priority order
                         -> APPROVE / NEEDS_HUMAN / REJECT + clauses
  stage 3  dry run     : projected balances vs reserve floors, SWIFT fees, FX spread, deferral and escalation cost
  feedback             : [{stage, obligation, code, clause, message, amount}] back to proposer.revise()
  revise               : at most `max_rounds` rounds per proposer
  best-of-N            : verified candidates ranked by (cost, #NEEDS_HUMAN, proposer order); cost model in policy yaml
Every proposal, verification report, feedback and the selection are appended to the hash-chained audit log, so the
UI can show the rejected first proposal and the feedback that fixed it."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from .audit import AuditLog
from .models import Action, Obligation, Proposal, Verdict
from .planner import BIG, PlanItem
from .policy import Policy, evaluate, required_amount, to_usd

D = Decimal
TRUSTED_EVIDENCE = ("balance_snapshot:", "obligation:", "forecast_confidence:", "fx_rate:", "reason:", "quote:",
                    "transfer:", "failure_type:")
CENT = D("0.01")


@dataclass(frozen=True)
class Feedback:
    stage: int                    # 1 validity, 2 policy legality, 3 dry-run
    obligation_id: str | None
    code: str                     # INVALID | ILLEGAL | NEEDS_HUMAN | FLOOR_BREACH
    clause: str | None
    message: str
    amount: Decimal | None = None  # e.g. shortfall in the obligation currency
    blocking: bool = True

    def as_dict(self):
        return {"stage": self.stage, "obligation": self.obligation_id, "code": self.code, "clause": self.clause,
                "message": self.message, "amount": None if self.amount is None else str(self.amount),
                "blocking": self.blocking}


@dataclass
class Report:
    verified: bool
    items: dict[str, PlanItem]
    feedback: list[Feedback]
    projected: dict[str, Decimal]
    cost: Decimal
    breakdown: dict


@dataclass
class Context:
    obligations: dict[str, Obligation]
    balances: dict[str, Decimal]
    rates: dict[str, Decimal]
    confidence: float
    snapshot: str
    policy: Policy
    base: str = "USD"

    def evidence(self, ob_id, *extra):
        return (f"balance_snapshot:{self.snapshot}", f"obligation:{ob_id}",
                f"forecast_confidence:{self.confidence:.2f}", *extra)

    def priority(self, ob: Obligation):
        nd = set(self.policy.c("never_defer")["categories"])
        return (0 if ob.category in nd else 1, ob.due_hours, ob.id)


@dataclass
class Round:
    proposer: str
    round: int
    plan: list[Proposal]
    report: Report


@dataclass
class Candidate:
    proposer: str
    rounds: int
    plan: list[Proposal]
    items: dict[str, PlanItem]
    projected: dict[str, Decimal]
    cost: Decimal
    verified: bool
    n_human: int = 0


@dataclass
class LoopResult:
    rounds: list[Round]
    candidates: list[Candidate]
    winner: Candidate | None

    def ui_view(self) -> dict:
        """JSON-able view for the dashboard: every round with its feedback, and the winner."""
        return {"rounds": [{"proposer": r.proposer, "round": r.round, "verified": r.report.verified,
                            "cost_usd": str(r.report.cost), "plan": [_p(p) for p in r.plan],
                            "verdicts": {k: [v.action.value, v.verdict.value, v.clauses] for k, v in sorted(r.report.items.items())},
                            "feedback": [f.as_dict() for f in r.report.feedback]} for r in self.rounds],
                "ranking": [{"proposer": c.proposer, "verified": c.verified, "cost_usd": str(c.cost), "rounds": c.rounds}
                            for c in self.candidates],
                "winner": None if not self.winner else {"proposer": self.winner.proposer, "cost_usd": str(self.winner.cost)}}


def _p(p: Proposal) -> dict:
    return {"obligation": p.obligation_id, "action": p.action.value, "sell": p.sell_currency, "buy": p.buy_currency,
            "buy_amount": None if p.buy_amount is None else str(p.buy_amount), "evidence": list(p.evidence)}


def snapshot(balances) -> str:
    s = json.dumps({k: str(v) for k, v in sorted(balances.items())})
    return hashlib.sha256(s.encode()).hexdigest()[:12]


class Verifier:
    def __init__(self, policy: Policy, obligations: Mapping[str, Obligation], rates: Mapping[str, Decimal]):
        self.policy, self.obs, self.rates = policy, dict(obligations), dict(rates)
        self.base = policy.raw.get("base_currency", "USD")
        self.floors = {k: D(str(v)) for k, v in policy.c("reserve_floor")["floors"].items()}
        if "costs" not in policy.raw:
            raise ValueError("policy has no `costs` section; the verifier's cost model lives in policy code")
        self.costs = {k: D(str(v)) for k, v in policy.raw["costs"].items()}
        self.rf = policy.c("reserve_floor")["id"]

    def _usd(self, amt, ccy):
        return to_usd(D(amt), ccy, self.rates)

    def _sell_amount(self, p: Proposal) -> Decimal:
        usd = self._usd(p.buy_amount, p.buy_currency)
        return usd if p.sell_currency == self.base else usd / D(str(self.rates[p.sell_currency]))

    # ---- stage 1 ----
    def _validity(self, plan, ctx) -> tuple[list[Feedback], set[str]]:
        fb, bad = [], set()
        known_ccy = set(ctx.balances) | set(self.rates) | {self.base}
        seen = {}
        for p in plan:
            seen[p.obligation_id] = seen.get(p.obligation_id, 0) + 1
        for oid in sorted(set(self.obs) - set(seen)):
            fb.append(Feedback(1, oid, "INVALID", "G1", f"{oid} has no action in the plan")); bad.add(oid)
        for oid, n in sorted(seen.items()):
            if oid not in self.obs:
                fb.append(Feedback(1, oid, "INVALID", "G1", f"unknown obligation {oid}")); bad.add(oid)
            elif n > 1:
                fb.append(Feedback(1, oid, "INVALID", "G1", f"{oid} appears {n} times")); bad.add(oid)
        for p in plan:
            if p.obligation_id not in self.obs:
                continue
            if not isinstance(p.action, Action):
                fb.append(Feedback(1, p.obligation_id, "INVALID", "G1", f"unknown action {p.action!r}")); bad.add(p.obligation_id); continue
            if p.action == Action.CONVERT:
                ob = self.obs[p.obligation_id]
                if not (p.sell_currency in known_ccy and p.buy_currency in known_ccy):
                    fb.append(Feedback(1, p.obligation_id, "INVALID", "G1", "unknown currency in conversion legs")); bad.add(p.obligation_id)
                elif p.buy_amount is None or D(p.buy_amount) <= 0:
                    fb.append(Feedback(1, p.obligation_id, "INVALID", "G1", "conversion amount must be > 0")); bad.add(p.obligation_id)
                elif p.buy_currency != ob.currency or p.sell_currency == p.buy_currency:
                    fb.append(Feedback(1, p.obligation_id, "INVALID", "G1", "conversion legs do not match the obligation")); bad.add(p.obligation_id)
            if p.action != Action.ESCALATE and f"obligation:{p.obligation_id}" not in p.evidence:
                fb.append(Feedback(1, p.obligation_id, "INVALID", "G2", "evidence does not cite the obligation")); bad.add(p.obligation_id)
            untrusted = [e for e in p.evidence if not e.startswith(TRUSTED_EVIDENCE)]
            if untrusted:
                fb.append(Feedback(1, p.obligation_id, "INVALID", "G2",
                                   f"evidence cites untrusted source(s) {untrusted[:2]}; invoice text is never evidence"))
                bad.add(p.obligation_id)
        return fb, bad

    # ---- full verification ----
    def verify(self, plan: list[Proposal], ctx: Context) -> Report:
        fb, bad = self._validity(plan, ctx)
        running = dict(ctx.balances)
        items: dict[str, PlanItem] = {}
        human_clauses = {self.policy.c(k)["id"] for k in ("fx_autonomy", "payout_autonomy", "new_beneficiary")}
        # items whose only stage-1 problem is untrusted evidence are still legality-checked (with that evidence
        # stripped) so one round of feedback covers everything; they stay blocked until the evidence is clean.
        untrusted_only = {f.obligation_id for f in fb if "untrusted" in f.message} - \
                         {f.obligation_id for f in fb if "untrusted" not in f.message}
        ordered = sorted((p for p in plan if p.obligation_id in self.obs and
                          (p.obligation_id not in bad or p.obligation_id in untrusted_only)),
                         key=lambda p: ctx.priority(self.obs[p.obligation_id]))
        for p in ordered:
            ob = self.obs[p.obligation_id]
            if p.obligation_id in untrusted_only:
                p = Proposal(p.obligation_id, p.action, p.sell_currency, p.buy_currency, p.buy_amount,
                             tuple(e for e in p.evidence if e.startswith(TRUSTED_EVIDENCE)))
            d = evaluate(p, ob, self.policy, running, self.rates, ctx.confidence)
            clauses, reasons = list(d.clauses), list(d.reasons)
            if p.action == Action.ESCALATE:  # cite the policy reason for an escalation when there is one
                probe = evaluate(Proposal(ob.id, Action.FUND, evidence=p.evidence or ("x",)), ob, self.policy,
                                 {ob.currency: BIG}, self.rates, ctx.confidence)
                if probe.verdict == Verdict.NEEDS_HUMAN:
                    clauses += probe.clauses; reasons += probe.reasons
            items[ob.id] = PlanItem(ob.id, p.action, p, d.verdict, clauses, reasons, cash_dependent=p.action != Action.ESCALATE)
            if p.action != Action.ESCALATE:
                for c, r in zip(d.clauses, d.reasons):
                    if c in human_clauses:
                        fb.append(Feedback(2, ob.id, "NEEDS_HUMAN", c, f"NEEDS_HUMAN clause {c}: {r}", blocking=False))
                    elif c == self.rf and p.action == Action.FUND:
                        short = (self.floors.get(ob.currency, D(0)) - (running.get(ob.currency, D(0)) - required_amount(ob, self.policy))).quantize(CENT)
                        fb.append(Feedback(2, ob.id, "ILLEGAL", c, f"ILLEGAL clause {c}: {r} (short {short} {ob.currency})", short))
                    else:
                        fb.append(Feedback(2, ob.id, "ILLEGAL", c, f"ILLEGAL clause {c}: {r}"))
            # stage-3 cash effects (applied for every executable action, approved or not, so breaches surface)
            if p.action == Action.FUND:
                running[ob.currency] = running.get(ob.currency, D(0)) - required_amount(ob, self.policy)
            elif p.action == Action.CONVERT and p.obligation_id not in bad and p.buy_amount:
                running[p.sell_currency] = running.get(p.sell_currency, D(0)) - self._sell_amount(p)
                running[p.buy_currency] = running.get(p.buy_currency, D(0)) + D(p.buy_amount) - required_amount(ob, self.policy)
        # ---- stage 3: dry run vs floors + cost ----
        projected = {k: v.quantize(CENT) for k, v in running.items()}
        for ccy, fl in sorted(self.floors.items()):
            if projected.get(ccy, D(0)) < fl:
                fb.append(Feedback(3, None, "FLOOR_BREACH", self.rf,
                                   f"floor breach: {ccy} projected {projected.get(ccy, D(0))} < floor {fl} (by {fl - projected.get(ccy, D(0))})",
                                   fl - projected.get(ccy, D(0))))
        cost, br = self._cost(items)
        verified = not any(f.blocking for f in fb)
        return Report(verified, items, fb, projected, cost, br)

    def _cost(self, items) -> tuple[Decimal, dict]:
        fee_ccy, fee = self.policy.swift_fee()
        br = {"fx_spread": D(0), "swift_fees": D(0), "deferral": D(0), "escalation": D(0)}
        for oid, it in items.items():
            ob, p = self.obs[oid], it.proposal
            if it.action == Action.CONVERT:
                br["fx_spread"] += self._usd(p.buy_amount, p.buy_currency) * self.costs["fx_spread_bps"] / 10000
            if it.action in (Action.FUND, Action.CONVERT) and ob.method == "SWIFT":
                br["swift_fees"] += self._usd(fee, fee_ccy)
            if it.action == Action.DEFER:
                br["deferral"] += self._usd(ob.amount, ob.currency) * self.costs["defer_penalty_bps"] / 10000
            if it.action == Action.ESCALATE or it.verdict == Verdict.NEEDS_HUMAN:
                br["escalation"] += self.costs["escalation_usd"]
        br = {k: v.quantize(CENT) for k, v in br.items()}
        return sum(br.values(), D(0)).quantize(CENT), {k: str(v) for k, v in br.items()}


class PlanLoop:
    def __init__(self, policy: Policy, obligations, rates, audit: AuditLog, proposers, max_rounds: int = 3):
        self.policy, self.rates, self.audit = policy, dict(rates), audit
        self.obs = {o.id: o for o in obligations}
        self.proposers, self.max_rounds = list(proposers), max_rounds
        self.verifier = Verifier(policy, self.obs, self.rates)

    def run(self, balances: Mapping[str, Decimal], confidence: float) -> LoopResult:
        ctx = Context(self.obs, dict(balances), self.rates, confidence, snapshot(balances), self.policy,
                      self.policy.raw.get("base_currency", "USD"))
        self.audit.append("loop_start", {"balances": ctx.balances, "confidence": confidence, "rates": self.rates,
                                         "proposers": [p.name for p in self.proposers], "max_rounds": self.max_rounds})
        rounds, cands = [], []
        for prop in self.proposers:
            plan, rep = None, None
            for r in range(1, self.max_rounds + 1):
                plan = prop.propose(ctx) if r == 1 else prop.revise(ctx, plan, rep.feedback)
                rep = self.verifier.verify(plan, ctx)
                rounds.append(Round(prop.name, r, plan, rep))
                self.audit.append("proposal", {"proposer": prop.name, "round": r, "plan": [_p(p) for p in plan]})
                self.audit.append("verification", {
                    "proposer": prop.name, "round": r, "verified": rep.verified, "cost_usd": rep.cost,
                    "cost_breakdown": rep.breakdown, "projected": rep.projected,
                    "verdicts": {k: [v.action.value, v.verdict.value, v.clauses] for k, v in sorted(rep.items.items())}})
                if not rep.verified:
                    self.audit.append("feedback", {"proposer": prop.name, "round": r,
                                                   "feedback": [f.as_dict() for f in rep.feedback]})
                    continue
                break
            n_h = sum(1 for it in rep.items.values() if it.verdict == Verdict.NEEDS_HUMAN)
            cands.append(Candidate(prop.name, r, plan, rep.items, rep.projected, rep.cost, rep.verified, n_h))
        ok = [(c.cost, c.n_human, i, c) for i, c in enumerate(cands) if c.verified]
        winner = min(ok, key=lambda t: t[:3])[3] if ok else None
        self.audit.append("plan_selected", {
            "winner": winner and winner.proposer, "cost_usd": winner and winner.cost,
            "ranking": [{"proposer": c.proposer, "verified": c.verified, "cost_usd": c.cost, "rounds": c.rounds} for c in cands],
            "plan": winner and [_p(p) for p in winner.plan],
            "note": None if winner else "no verified plan: nothing executes; escalate to a human"})
        return LoopResult(rounds, cands, winner)

    def execute_winner(self, result: LoopResult, executor, beneficiary_ids: Mapping[str, str]) -> dict:
        """Only the winning VERIFIED plan executes: APPROVE FUND/CONVERT go to the idempotent executor; NEEDS_HUMAN
        items get a bound approval request; DEFER/ESCALATE never execute."""
        if not result.winner or not result.winner.verified:
            self.audit.append("execute_plan", {"executed": [], "reason": "no verified winner"})
            return {}
        out = {}
        for oid, it in sorted(result.winner.items.items()):
            ob = self.obs[oid]
            if it.action not in (Action.FUND, Action.CONVERT):
                out[oid] = "NOT_EXECUTABLE"
            elif it.verdict == Verdict.APPROVE:
                out[oid] = executor.execute(it, ob, beneficiary_id=beneficiary_ids.get(oid))["status"]
            elif it.verdict == Verdict.NEEDS_HUMAN:
                executor.approvals.request(executor.binding_for(it, ob)); out[oid] = "AWAITING_APPROVAL"
        self.audit.append("execute_plan", {"winner": result.winner.proposer, "outcome": out})
        return out
