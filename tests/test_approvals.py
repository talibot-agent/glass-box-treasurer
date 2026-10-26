from decimal import Decimal as D
import pytest
from glassbox.approvals import ApprovalBook, ApprovalMismatch, binding

def b(**kw):
    base = dict(obligation_id="OB-SUPP", action="CONVERT", amount=D("3212.85"), currency="EUR",
                counterparty="Lyon Textiles", evidence=("balance_snapshot:abc", "fx_rate:1.08"))
    base.update(kw); return binding(**base)

def test_binding_is_deterministic_and_sensitive_to_every_field():
    h = b().digest
    assert b().digest == h
    for kw in [dict(amount=D("3212.86")), dict(currency="GBP"), dict(counterparty="Evil"),
               dict(evidence=("balance_snapshot:zzz",)), dict(action="FUND"), dict(obligation_id="X")]:
        assert b(**kw).digest != h

def test_amount_normalised():
    assert b(amount=D("3212.850")).digest == b().digest

def test_approve_then_consume_once(tmp_path):
    book = ApprovalBook(tmp_path / "ap.json")
    tok = book.request(b())
    book.approve(tok, approver="mykel")
    book.consume(b())             # exact match ok
    with pytest.raises(ApprovalMismatch):
        book.consume(b())         # single use

def test_unapproved_or_altered_rejected(tmp_path):
    book = ApprovalBook(tmp_path / "ap.json")
    tok = book.request(b())
    with pytest.raises(ApprovalMismatch):
        book.consume(b())         # pending, not approved
    book.approve(tok, approver="mykel")
    with pytest.raises(ApprovalMismatch):
        book.consume(b(amount=D("3300")))

def test_persisted(tmp_path):
    p = tmp_path / "ap.json"
    tok = ApprovalBook(p).request(b()); ApprovalBook(p).approve(tok, "mykel")
    ApprovalBook(p).consume(b())
