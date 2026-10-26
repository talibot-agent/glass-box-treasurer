from decimal import Decimal as D
import pytest
from glassbox.airwallex import FakeAirwallex
from glassbox.approvals import ApprovalBook, ApprovalMismatch
from glassbox.audit import AuditLog
from glassbox.executor import Executor, ExecutionRefused
from glassbox.models import Action, Proposal, Verdict, Obligation
from glassbox.planner import PlanItem

SUPP = Obligation("OB-SUPP", "Lyon Textiles", "supplier", "EUR", D("3200"), 36, method="SWIFT")
SHIP = Obligation("OB-SHIP", "Maersk", "shipping", "USD", D("6500"), 24)

def conv(verdict=Verdict.APPROVE):
    p = Proposal("OB-SUPP", Action.CONVERT, "USD", "EUR", D("3212.85"), ("balance_snapshot:a",))
    return PlanItem("OB-SUPP", Action.CONVERT, p, verdict, [], [])

def fund(ob=SHIP):
    return PlanItem(ob.id, Action.FUND, Proposal(ob.id, Action.FUND, evidence=("balance_snapshot:a",)), Verdict.APPROVE, [], [])

@pytest.fixture
def env(tmp_path):
    awx = FakeAirwallex({"USD": D("15500"), "EUR": D("5000")}, {("USD", "EUR"): D("0.925")})
    ex = Executor(awx, AuditLog(tmp_path / "a.jsonl"), ApprovalBook(tmp_path / "ap.json"), tmp_path / "ledger.json")
    return awx, ex, tmp_path

def test_convert_executes_once_with_fresh_quote(env):
    awx, ex, _ = env
    r1 = ex.execute(conv(), SUPP)
    assert r1["status"] == "DONE" and awx.balances()["EUR"] == D("8212.85")
    r2 = ex.execute(conv(), SUPP)           # duplicate lock
    assert r2["status"] == "ALREADY_DONE" and awx.balances()["EUR"] == D("8212.85")

def test_duplicate_lock_survives_restart(env, tmp_path):
    awx, ex, tp = env
    ex.execute(fund(), SHIP, beneficiary_id="B1")
    ex2 = Executor(awx, AuditLog(tp / "a.jsonl"), ApprovalBook(tp / "ap.json"), tp / "ledger.json")
    assert ex2.execute(fund(), SHIP, beneficiary_id="B1")["status"] == "ALREADY_DONE"
    assert len(awx.transfers) == 1

def test_fund_transfer_uses_obligation_method_and_stable_request_id(env):
    awx, ex, _ = env
    ex.execute(fund(SUPP), SUPP, beneficiary_id="B2")
    t = awx.transfers[0]
    assert t["currency"] == "EUR" and t["amount"] == D("3200") and t["method"] == "SWIFT"
    assert 10 <= len(t["request_id"]) <= 50

def test_retry_after_transport_error_reuses_request_id(env):
    awx, ex, _ = env
    calls = []
    orig = awx.create_transfer
    def flaky(*a, **kw):
        calls.append(a[3] if len(a) > 3 else kw["request_id"])
        if len(calls) == 1:
            orig(*a, **kw); raise TimeoutError("lost response")
        return orig(*a, **kw)
    awx.create_transfer = flaky
    with pytest.raises(TimeoutError):
        ex.execute(fund(), SHIP, beneficiary_id="B1")
    ex.execute(fund(), SHIP, beneficiary_id="B1")
    assert calls[0] == calls[1] and len(awx.transfers) == 1

def test_needs_human_requires_bound_approval(env):
    awx, ex, _ = env
    with pytest.raises(ExecutionRefused):
        ex.execute(conv(Verdict.NEEDS_HUMAN), SUPP)
    tok = ex.approvals.request(ex.binding_for(conv(Verdict.NEEDS_HUMAN), SUPP))
    ex.approvals.approve(tok, "mykel")
    assert ex.execute(conv(Verdict.NEEDS_HUMAN), SUPP)["status"] == "DONE"

def test_approval_does_not_transfer_to_altered_proposal(env):
    awx, ex, _ = env
    tok = ex.approvals.request(ex.binding_for(conv(Verdict.NEEDS_HUMAN), SUPP))
    ex.approvals.approve(tok, "mykel")
    p = Proposal("OB-SUPP", Action.CONVERT, "USD", "EUR", D("4000"), ("balance_snapshot:a",))
    with pytest.raises(ExecutionRefused):
        ex.execute(PlanItem("OB-SUPP", Action.CONVERT, p, Verdict.NEEDS_HUMAN, [], []), SUPP)

def test_reject_defer_escalate_never_execute(env):
    awx, ex, _ = env
    for a, v in [(Action.CONVERT, Verdict.REJECT), (Action.DEFER, Verdict.APPROVE), (Action.ESCALATE, Verdict.NEEDS_HUMAN)]:
        p = Proposal("OB-SUPP", a, "USD", "EUR", D("1"), ("e",))
        with pytest.raises(ExecutionRefused):
            ex.execute(PlanItem("OB-SUPP", a, p, v, [], []), SUPP)
    assert awx.transfers == []

def test_fund_needs_beneficiary(env):
    _, ex, _ = env
    with pytest.raises(ExecutionRefused):
        ex.execute(fund(), SHIP)

def test_audit_records_execution(env, tmp_path):
    awx, ex, tp = env
    ex.execute(conv(), SUPP)
    kinds = [e["kind"] for e in AuditLog(tp / "a.jsonl").entries()]
    assert "execute" in kinds and AuditLog(tp / "a.jsonl").verify()

def test_transfer_fee_reconciled_and_mismatch_audited(tmp_path):
    from glassbox.fees import FeeBook
    from glassbox.policy import Policy
    import pathlib
    pol = Policy.load(pathlib.Path(__file__).resolve().parents[1] / "policies/default.yaml")
    awx = FakeAirwallex({"EUR": D("5000")}, {})
    orig = awx.create_transfer
    awx.create_transfer = lambda *a, **k: {**orig(*a, **k), "fee_amount": 15.0, "fee_currency": "EUR"}
    fb = FeeBook(tmp_path / "fees.json")
    ex = Executor(awx, AuditLog(tmp_path / "a.jsonl"), ApprovalBook(tmp_path / "ap.json"), tmp_path / "l.json",
                  fees=fb, policy=pol)
    ex.execute(fund(SUPP), SUPP, beneficiary_id="B2")
    kinds = [e["kind"] for e in AuditLog(tmp_path / "a.jsonl").entries()]
    assert "fee_mismatch" in kinds and fb.policy(pol).swift_fee()[1] == D("16.00")
