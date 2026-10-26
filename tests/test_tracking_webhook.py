import hashlib, hmac, json
from decimal import Decimal as D
import pytest
from glassbox.tracking import TransferTracker, TERMINAL
from glassbox.webhook import WebhookReceiver
from glassbox.audit import AuditLog

class Seq:
    def __init__(self, states): self.states, self.n = list(states), 0
    def get_transfer(self, tid):
        s = self.states[min(self.n, len(self.states) - 1)]; self.n += 1
        return s if isinstance(s, dict) else {"id": tid, "status": s}

def test_polls_until_paid_treating_sent_as_in_flight(tmp_path):
    t = TransferTracker(Seq(["SCHEDULED", "PROCESSING", "SENT", "PAID"]), AuditLog(tmp_path / "a.jsonl"), sleep=lambda s: None)
    r = t.wait("T1", max_polls=10)
    assert r["status"] == "PAID" and r["final"] and r["history"] == ["SCHEDULED", "PROCESSING", "SENT", "PAID"]

def test_cancelled_is_terminal_failure_with_failure_type(tmp_path):
    t = TransferTracker(Seq(["SENT", {"id": "T1", "status": "CANCELLED", "failure_type": "RETURNED", "failure_reason": "bank return"}]),
                        AuditLog(tmp_path / "a.jsonl"), sleep=lambda s: None)
    r = t.wait("T1")
    assert r["status"] == "CANCELLED" and r["outcome"] == "FAILED" and r["failure_type"] == "RETURNED"

def test_timeout_is_not_final(tmp_path):
    t = TransferTracker(Seq(["SENT"]), AuditLog(tmp_path / "a.jsonl"), sleep=lambda s: None)
    r = t.wait("T1", max_polls=3)
    assert r["final"] is False and r["status"] == "SENT" and r["outcome"] == "IN_FLIGHT"

def test_no_failed_status_assumed():
    assert "FAILED" not in TERMINAL and {"PAID", "CANCELLED"} <= TERMINAL

def test_fee_observed_from_transfer(tmp_path):
    seen = []
    t = TransferTracker(Seq([{"id": "T", "status": "PAID", "fee_amount": 13.43, "fee_currency": "EUR", "transfer_method": "SWIFT"}]),
                        AuditLog(tmp_path / "a.jsonl"), sleep=lambda s: None, on_fee=lambda *a: seen.append(a))
    t.wait("T")
    assert seen == [("EUR", "SWIFT", D("13.43"))]

SECRET = "whsec_test"
def sign(ts, body): return hmac.new(SECRET.encode(), ts.encode() + body, hashlib.sha256).hexdigest()

def rx(tmp_path, got, now=1_000_000.0):
    return WebhookReceiver(SECRET, tmp_path / "seen.json", AuditLog(tmp_path / "a.jsonl"), on_event=got.append, clock=lambda: now)

def test_valid_signature_on_raw_bytes_and_dedupe(tmp_path):
    got = []; r = rx(tmp_path, got)
    body = b'{"id":"evt_1","name":"payout.transfer.paid","data":{"object":{"id":"T1","status":"PAID"}}}'
    ts = str(int(1_000_000 * 1000))
    assert r.handle({"x-timestamp": ts, "x-signature": sign(ts, body)}, body) == 200
    assert r.handle({"x-timestamp": ts, "x-signature": sign(ts, body)}, body) == 200   # duplicate acked
    assert len(got) == 1 and got[0]["id"] == "evt_1"

def test_reserialised_body_fails(tmp_path):
    got = []; r = rx(tmp_path, got)
    body = b'{"id": "evt_2",  "name":"x"}'
    ts = str(1_000_000 * 1000)
    sig = sign(ts, json.dumps(json.loads(body)).encode())
    assert r.handle({"x-timestamp": ts, "x-signature": sig}, body) == 401 and got == []

def test_stale_timestamp_and_missing_headers_rejected(tmp_path):
    got = []; r = rx(tmp_path, got)
    body = b'{"id":"evt_3"}'; old = str((1_000_000 - 600) * 1000)
    assert r.handle({"x-timestamp": old, "x-signature": sign(old, body)}, body) == 401
    assert r.handle({}, body) == 401 and got == []

def test_dedupe_survives_restart(tmp_path):
    got = []; body = b'{"id":"evt_4"}'; ts = str(1_000_000 * 1000)
    rx(tmp_path, got).handle({"x-timestamp": ts, "x-signature": sign(ts, body)}, body)
    rx(tmp_path, got).handle({"x-timestamp": ts, "x-signature": sign(ts, body)}, body)
    assert len(got) == 1
