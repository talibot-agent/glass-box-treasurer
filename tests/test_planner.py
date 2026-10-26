from decimal import Decimal as D
from pathlib import Path
from glassbox.audit import AuditLog
from glassbox.models import Action, Verdict
from glassbox.planner import Planner
from glassbox.policy import Policy
from glassbox.seed import OBLIGATIONS, SEED_BALANCES

POL = Policy.load(Path(__file__).parents[1] / "policies/default.yaml")
RATES = {"EUR": D("1.08"), "GBP": D("1.27")}


def mk(tmp_path, conf=0.9):
    log = AuditLog(tmp_path / "audit.jsonl")
    p = Planner(POL, OBLIGATIONS, RATES, log)
    return p, p.plan(dict(SEED_BALANCES), conf), log


def actions(plan):
    return {k: v.action for k, v in plan.items()}


def test_initial_plan_matches_kit1(tmp_path):
    _, plan, _ = mk(tmp_path)
    assert actions(plan) == {"OB-SHIP": Action.FUND, "OB-PAY": Action.FUND, "OB-SUPP": Action.CONVERT,
                             "OB-SAAS": Action.DEFER, "OB-NEWV": Action.ESCALATE}


def test_convert_is_minimum_including_swift_fee(tmp_path):
    _, plan, _ = mk(tmp_path)
    c = plan["OB-SUPP"].proposal
    # need 4200 + 14.43 fee (13.43 live + 1.00 buffer); keep EUR floor 500 out of 1500 -> spendable 1000
    assert (c.sell_currency, c.buy_currency, c.buy_amount) == ("USD", "EUR", D("3214.43"))
    assert plan["OB-SUPP"].verdict == Verdict.APPROVE


def test_every_item_cites_evidence_and_policy_checked(tmp_path):
    _, plan, _ = mk(tmp_path)
    for item in plan.values():
        assert item.proposal.evidence and item.verdict in Verdict


def test_reserve_floor_preserved(tmp_path):
    p, plan, _ = mk(tmp_path)
    left = p.projected_balances(dict(SEED_BALANCES))
    assert left["USD"] >= 2000 and left["EUR"] >= 500 and left["GBP"] >= 500


def test_escalation_cites_new_beneficiary_or_cap(tmp_path):
    _, plan, _ = mk(tmp_path)
    assert {"C4", "C5"} & set(plan["OB-NEWV"].clauses)


def test_low_confidence_flips_conversion_to_human(tmp_path):
    p, _, _ = mk(tmp_path)
    changed = p.update_confidence(0.55)
    assert changed["recalculated"] == ["OB-SUPP"]
    assert p.current["OB-SUPP"].action == Action.CONVERT and p.current["OB-SUPP"].verdict == Verdict.NEEDS_HUMAN
    assert "C3" in p.current["OB-SUPP"].clauses


def test_deposit_recalculates_only_affected(tmp_path):
    p, before, _ = mk(tmp_path)
    funded_before = {k: before[k] for k in ("OB-SHIP", "OB-PAY", "OB-NEWV")}
    bal = dict(SEED_BALANCES); bal["USD"] += D("5000")
    r = p.on_deposit("USD", bal)
    assert set(r["recalculated"]) == {"OB-SUPP", "OB-SAAS"}
    assert r["changed"] == ["OB-SAAS"] and p.current["OB-SAAS"].action == Action.FUND
    for k, v in funded_before.items():
        assert p.current[k] is v  # untouched objects, not recomputed


def test_deposit_in_unused_currency_recalculates_nothing(tmp_path):
    p, _, _ = mk(tmp_path)
    bal = dict(SEED_BALANCES); bal["CNY"] = D("1000")
    assert p.on_deposit("CNY", bal)["recalculated"] == []


def test_audit_log_records_and_replays(tmp_path):
    p, plan, log = mk(tmp_path)
    p.on_deposit("USD", {**SEED_BALANCES, "USD": SEED_BALANCES["USD"] + 5000})
    assert log.verify()
    kinds = [e["kind"] for e in log.entries()]
    assert kinds.count("decision") >= 5 and "recalc" in kinds
    # replay: same inputs produce identical decisions
    p2 = Planner(POL, OBLIGATIONS, RATES, AuditLog(tmp_path / "b.jsonl"))
    plan2 = p2.plan(dict(SEED_BALANCES), 0.9)
    assert {k: (v.action, v.verdict, v.proposal) for k, v in plan.items()} == \
           {k: (v.action, v.verdict, v.proposal) for k, v in plan2.items()}


def test_never_defer_payroll_escalates_when_unfundable(tmp_path):
    log = AuditLog(tmp_path / "c.jsonl")
    p = Planner(POL, OBLIGATIONS, RATES, log)
    plan = p.plan({"USD": D("3000"), "EUR": D("500"), "GBP": D("500")}, 0.9)
    assert plan["OB-PAY"].action == Action.ESCALATE and plan["OB-SHIP"].action == Action.ESCALATE
