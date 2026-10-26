import json, uuid
from decimal import Decimal as D
import pytest
from glassbox.audit import AuditLog
from glassbox.airwallex import FakeAirwallex, QuoteAlreadyUsed, InsufficientFunds


def test_audit_chain_verifies_and_detects_tamper(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append("proposal", {"o": "O1"}); log.append("decision", {"v": "APPROVE"})
    assert log.verify()
    lines = log.path.read_text().splitlines()
    e = json.loads(lines[0]); e["payload"]["o"] = "O2"; lines[0] = json.dumps(e)
    log.path.write_text("\n".join(lines) + "\n")
    assert not log.verify()


def fake():
    return FakeAirwallex({"USD": D("10000"), "EUR": D("0")}, {("USD", "EUR"): D("0.925")})


def test_transfer_idempotent_on_retry():
    f = fake(); rid = str(uuid.uuid4())
    a = f.create_transfer("B1", "USD", D("100"), rid); b = f.create_transfer("B1", "USD", D("100"), rid)
    assert a == b and len(f.transfers) == 1 and f.balances()["USD"] == D("9900")


def test_quote_single_use():
    f = fake(); q = f.create_quote("USD", "EUR", D("925"))
    f.create_conversion(q["quote_id"], str(uuid.uuid4()))
    assert f.balances()["EUR"] == D("925") and f.balances()["USD"] == D("9000.00")
    with pytest.raises(QuoteAlreadyUsed):
        f.create_conversion(q["quote_id"], str(uuid.uuid4()))


def test_insufficient_funds():
    with pytest.raises(InsufficientFunds):
        fake().create_transfer("B1", "USD", D("20000"), str(uuid.uuid4()))
