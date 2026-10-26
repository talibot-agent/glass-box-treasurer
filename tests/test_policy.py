from decimal import Decimal as D
from pathlib import Path
import pytest
from glassbox.models import Action, Obligation, Proposal, Verdict
from glassbox.policy import Policy, evaluate, required_amount

POL = Policy.load(Path(__file__).parents[1] / "policies/default.yaml")
RATES = {"EUR": D("1.08"), "GBP": D("1.27")}
BAL = {"USD": D("20000"), "EUR": D("1000"), "GBP": D("800")}


def ob(**kw):
    base = dict(id="O1", counterparty="Acme", category="supplier", currency="USD", amount=D("1000"), due_hours=24)
    base.update(kw)
    return Obligation(**base)


def ev(p, o, conf=0.9, bal=BAL):
    return evaluate(p, o, POL, bal, RATES, conf)


def test_fund_within_policy_is_approved():
    d = ev(Proposal("O1", Action.FUND, evidence=("bal:snap1",)), ob())
    assert d.verdict == Verdict.APPROVE and d.clauses == []


def test_fund_breaching_reserve_floor_rejected():
    d = ev(Proposal("O1", Action.FUND, evidence=("e",)), ob(amount=D("18500")))
    assert d.verdict == Verdict.REJECT and "C1" in d.clauses


def test_missing_evidence_rejected():
    d = ev(Proposal("O1", Action.FUND), ob())
    assert d.verdict == Verdict.REJECT and "G2" in d.clauses


def test_payroll_never_deferred():
    d = ev(Proposal("O1", Action.DEFER, evidence=("e",)), ob(category="payroll"))
    assert d.verdict == Verdict.REJECT and "C2" in d.clauses


def test_defer_saas_ok():
    assert ev(Proposal("O1", Action.DEFER, evidence=("e",)), ob(category="saas")).verdict == Verdict.APPROVE


def test_large_payout_needs_human():
    d = ev(Proposal("O1", Action.FUND, evidence=("e",)), ob(amount=D("12000")))
    assert d.verdict == Verdict.NEEDS_HUMAN and "C4" in d.clauses


def test_new_beneficiary_needs_human():
    d = ev(Proposal("O1", Action.FUND, evidence=("e",)), ob(beneficiary_verified=False))
    assert d.verdict == Verdict.NEEDS_HUMAN and "C5" in d.clauses


def test_reject_dominates_needs_human():
    d = ev(Proposal("O1", Action.FUND, evidence=("e",)), ob(amount=D("19000"), beneficiary_verified=False))
    assert d.verdict == Verdict.REJECT and {"C1", "C5"} <= set(d.clauses)


def conv(buy_amount, ccy="EUR"):
    return Proposal("O1", Action.CONVERT, "USD", ccy, D(buy_amount), evidence=("quote:Q-1",))


@pytest.mark.parametrize("conf,verdict", [(0.9, Verdict.APPROVE), (0.6, Verdict.NEEDS_HUMAN), (0.3, Verdict.NEEDS_HUMAN)])
def test_fx_limit_tied_to_confidence(conf, verdict):
    # need 3000 EUR, have 1000 -> buy 2000 EUR ~= 2160 USD
    o = ob(currency="EUR", amount=D("3000"))
    d = ev(conv("2000"), o, conf=conf)
    assert d.verdict == verdict
    if verdict == Verdict.NEEDS_HUMAN:
        assert "C3" in d.clauses


def test_contradicting_email_flips_same_conversion_to_human():
    o = ob(currency="EUR", amount=D("3000"))
    assert ev(conv("2000"), o, conf=0.85).verdict == Verdict.APPROVE
    assert ev(conv("2000"), o, conf=0.55).verdict == Verdict.NEEDS_HUMAN


def test_swift_fee_included():
    o = ob(currency="EUR", amount=D("3000"), method="SWIFT")
    assert required_amount(o, POL) == D("3014.43")
    d = ev(conv("2000"), o)
    assert d.verdict == Verdict.REJECT and "C6" in d.clauses
    assert ev(conv("2014.43"), o).verdict == Verdict.APPROVE


def test_conversion_wrong_currency_rejected():
    o = ob(currency="EUR", amount=D("3000"))
    assert ev(conv("2000", "GBP"), o).verdict == Verdict.REJECT


def test_conversion_breaching_usd_floor_rejected():
    o = ob(currency="EUR", amount=D("20000"))
    d = ev(conv("19000"), o, conf=0.9, bal={"USD": D("20000"), "EUR": D("1000")})
    assert d.verdict == Verdict.REJECT and "C1" in d.clauses


def test_escalate_always_needs_human():
    assert ev(Proposal("O1", Action.ESCALATE), ob()).verdict == Verdict.NEEDS_HUMAN


def test_policy_validation():
    with pytest.raises(ValueError):
        Policy.from_dict({"clauses": {"reserve_floor": {}}})


def test_deterministic():
    p, o = conv("2000"), ob(currency="EUR", amount=D("3000"))
    a, b = ev(p, o, 0.6), ev(p, o, 0.6)
    assert (a.verdict, a.clauses, a.reasons) == (b.verdict, b.clauses, b.reasons)
