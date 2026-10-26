import json, os
from decimal import Decimal as D
import pytest
from glassbox.http_client import AirwallexHTTP, AirwallexError


class FakeTransport:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, json.loads(data) if data else None))
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return resp() if callable(resp) else resp
        return 404, {"message": "no route"}


LOGIN = {("POST", "/authentication/login"): (201, {"token": "tok"})}


def client(extra, clock=lambda: 0.0):
    t = FakeTransport({**LOGIN, **extra})
    return AirwallexHTTP("cid", "key", base="https://x", transport=t, clock=clock), t


def test_balances_parsed_and_auth_header():
    c, t = client({("GET", "/balances/current"): (200, [{"currency": "USD", "available_amount": 100.5}])})
    assert c.balances() == {"USD": D("100.5")}
    assert t.calls[0][2]["x-client-id"] == "cid"
    assert t.calls[1][2]["Authorization"] == "Bearer tok"


def test_token_reused_then_refreshed_on_expiry():
    now = [0.0]
    c, t = client({("GET", "/balances/current"): (200, [])}, clock=lambda: now[0])
    c.balances(); c.balances()
    assert sum("login" in x[1] for x in t.calls) == 1
    now[0] = 26 * 60; c.balances()
    assert sum("login" in x[1] for x in t.calls) == 2


def test_401_triggers_single_relogin():
    seq = iter([(401, {}), (200, [])])
    c, t = client({("GET", "/balances/current"): lambda: next(seq)})
    assert c.balances() == {}
    assert sum("login" in x[1] for x in t.calls) == 2


def test_login_failure_raises():
    t = FakeTransport({("POST", "/authentication/login"): (401, {"code": "unauthorized"})})
    with pytest.raises(AirwallexError):
        AirwallexHTTP("a", "b", base="https://x", transport=t).balances()


def test_transfer_payload_major_units_and_request_id():
    c, t = client({("POST", "/transfers/create"): (201, {"id": "T1"})})
    c.create_transfer("B1", "USD", D("12.50"), "rid-1")
    body = t.calls[-1][3]
    assert body["transfer_amount"] == 12.5 and body["request_id"] == "rid-1"


def test_fx_calls_have_no_api_version_header():
    c, t = client({("POST", "/fx/quotes/create"): (201, {"quote_id": "Q1", "client_rate": 0.92})})
    q = c.create_quote("USD", "EUR", D("100"))
    assert q["quote_id"] == "Q1" and "x-api-version" not in t.calls[-1][2]


def test_repr_hides_secrets():
    c, _ = client({})
    assert "key" not in repr(c) and "cid" not in repr(c)


def test_default_base_is_official_sandbox(monkeypatch):
    monkeypatch.delenv("AWX_BASE_URL", raising=False)
    assert AirwallexHTTP("a", "b").base == "https://api.sandbox.airwallex.com"


def test_conversion_payload_matches_docs():
    c, t = client({("POST", "/fx/quotes/create"): (201, {"quote_id": "Q1", "client_rate": 0.92}),
                   ("POST", "/fx/conversions/create"): (201, {"status": "SETTLED"})})
    c.create_quote("USD", "EUR", D("7525.70"))
    c.create_conversion("Q1", "rid")
    body = t.calls[-1][3]
    assert body == {"request_id": "rid", "quote_id": "Q1", "buy_currency": "EUR",
                    "sell_currency": "USD", "buy_amount": "7525.70"}


def test_deposit_payload_has_no_currency_and_uses_global_account():
    c, t = client({("GET", "/global_accounts"): (200, {"items": [{"id": "GA1", "currency": "USD"}]}),
                   ("POST", "/simulation/deposit/create"): (201, {"status": "PENDING"})})
    c.simulate_deposit("USD", D("25000"))
    body = t.calls[-1][3]
    assert body["global_account_id"] == "GA1" and body["amount"] == 25000 and "currency" not in body


def test_beneficiary_schema_validate_create_and_transfer_method():
    c, t = client({("POST", "/beneficiaries/schema"): (200, {"schema": {}}),
                   ("POST", "/beneficiaries/validate"): (200, "OK"),
                   ("POST", "/beneficiaries/create"): (201, {"beneficiary_id": "B9"}),
                   ("POST", "/transfers/create"): (201, {"id": "T1", "status": "PROCESSING"})})
    c.beneficiary_schema("DE", "EUR", "SWIFT")
    assert t.calls[-1][3] == {"bank_country_code": "DE", "account_currency": "EUR", "transfer_method": "SWIFT", "entity_type": "COMPANY"}
    assert c.validate_beneficiary({"beneficiary": {}, "transfer_methods": ["SWIFT"]}) is True
    assert c.create_beneficiary({"beneficiary": {}, "transfer_methods": ["SWIFT"]}) == "B9"
    c.create_transfer("B9", "EUR", D("10"), "r" * 36, method="SWIFT")
    body = t.calls[-1][3]
    assert body["transfer_method"] == "SWIFT" and body["transfer_currency"] == "EUR" and body["request_id"] == "r" * 36
