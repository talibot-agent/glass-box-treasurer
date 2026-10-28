"""Cycle 2026-10-28: S20 live E2E helpers (sim fallback, balance reconciliation), MCP stdio client, executor namespace."""
import sys, textwrap, uuid
from decimal import Decimal as D

import pytest

from glassbox import live_e2e
from glassbox.http_client import AirwallexError
from glassbox.mcp_client import DeveloperMCP, MCPError


class FakeMCP:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def call(self, name, args=None):
        self.calls.append((name, args))
        if name in self.fail:
            raise MCPError(f"{name}: 500")
        return {}


class FakeREST:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def simulate_transfer(self, tid, st):
        self.calls.append((tid, st))
        if self.fail:
            raise AirwallexError(500, {"code": "operation_failed"})


def test_simulate_paid_via_mcp_needs_no_rest():
    m, r = FakeMCP(), FakeREST()
    assert live_e2e.simulate_paid(m, r, "T1", sleep=lambda s: None) == []
    assert [a["next_status"] for _, a in m.calls] == ["SENT", "PAID"] and r.calls == []


def test_simulate_paid_falls_back_to_rest():
    m, r = FakeMCP(fail={"simulate_transfer_result"}), FakeREST()
    assert live_e2e.simulate_paid(m, r, "T1", sleep=lambda s: None) == []
    assert r.calls == [("T1", "SENT"), ("T1", "PAID")]


def test_simulate_paid_reports_external_failure_and_stops():
    m, r = FakeMCP(fail={"simulate_transfer_result"}), FakeREST(fail=True)
    errs = live_e2e.simulate_paid(m, r, "T1", sleep=lambda s: None)
    assert len(errs) == 1 and errs[0]["status"] == "SENT" and errs[0]["rest"] == "500 operation_failed"
    assert r.calls == [("T1", "SENT")]          # never tries PAID after SENT failed


def test_expected_delta_from_ledger():
    ledger = {
        "CONVERT:OB-SUPP": {"status": "DONE", "result": {"buy_currency": "EUR", "sell_currency": "USD",
                                                         "buy_amount": "3214.43", "sell_amount": "3598.08"}},
        "PAY:OB-SUPP": {"status": "DONE", "result": {"source_currency": "EUR", "amount_payer_pays": "4213.48"}},
        "FUND:OB-SHIP": {"status": "DONE", "result": {"source_currency": "USD", "amount_payer_pays": "1000"}},
        "PAY:OB-NEWV": {"status": "AWAITING_APPROVAL", "result": None},
    }
    d = live_e2e.expected_delta(ledger, D("2500"))
    assert d == {"USD": D("2500") - D("3598.08") - D("1000"), "EUR": D("3214.43") - D("4213.48"), "GBP": D(0)}


FAKE_SERVER = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m: continue
        if m["method"] == "initialize": r = {"capabilities": {}}
        elif m["method"] == "tools/list": r = {"tools": [{"name": "get_balances"}]}
        elif m["params"]["name"] == "boom": r = {"isError": True, "content": [{"type": "text", "text": "nope"}]}
        else: r = {"content": [{"type": "text", "text": json.dumps({"items": [{"currency": "USD", "available_amount": 5}]})}]}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    srv = tmp_path / "srv.py"; srv.write_text(FAKE_SERVER)
    monkeypatch.setattr("glassbox.mcp_client.load_creds", lambda: ("cid", "key"))
    m = DeveloperMCP(cmd=[sys.executable, str(srv)], timeout=10)
    yield m
    m.close()


def test_mcp_client_lists_calls_and_errors(mcp):
    assert mcp.tools() == ["get_balances"]
    assert live_e2e._bal(mcp.call("get_balances")) == {"USD": D(5)}
    with pytest.raises(MCPError):
        mcp.call("boom")
    assert mcp.calls == ["get_balances", "boom"]
    assert "key" not in repr(mcp) and "cid" not in repr(mcp)


def test_executor_namespace_changes_request_ids():
    import inspect
    from glassbox.executor import NS, Executor
    assert inspect.signature(Executor.__init__).parameters["ns"].default == NS   # offline default unchanged
    a, b = uuid.uuid5(NS, "PAY:X"), uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "glassbox-live-r1"), "PAY:X")
    assert a != b
