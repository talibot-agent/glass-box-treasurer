from decimal import Decimal as D
import pytest
from glassbox.fees import FeeBook
from glassbox.policy import Policy, required_amount
from glassbox.models import Obligation
from glassbox.rates import rate_to_usd, quote_cost
from pathlib import Path

POL = Policy.load(Path(__file__).resolve().parents[1] / "policies/default.yaml")
SW = Obligation("X", "Lyon", "supplier", "EUR", D("100"), 10, method="SWIFT")

def test_policy_fee_is_observed_plus_buffer():
    ccy, fee = POL.swift_fee()
    assert (ccy, fee) == ("EUR", D("14.43"))          # 13.43 observed live + 1.00 buffer
    assert required_amount(SW, POL) == D("114.43")

def test_feebook_raises_policy_fee_when_live_fee_higher(tmp_path):
    fb = FeeBook(tmp_path / "fees.json")
    assert fb.policy(POL).swift_fee()[1] == D("14.43")
    assert fb.observe("EUR", "SWIFT", D("13.43"), POL) is False   # within estimate
    assert fb.observe("EUR", "SWIFT", D("15.10"), POL) is True    # mismatch flagged
    assert FeeBook(tmp_path / "fees.json").policy(POL).swift_fee()[1] == D("16.10")
    assert POL.swift_fee()[1] == D("14.43")                      # original policy untouched

def test_feebook_never_lowers_fee(tmp_path):
    fb = FeeBook(tmp_path / "f.json"); fb.observe("EUR", "SWIFT", D("5"), POL)
    assert fb.policy(POL).swift_fee()[1] == D("14.43")

# live shapes captured 2026-10-26 from /fx/rates/current
EURUSD = {"sell_currency": "USD", "buy_currency": "EUR", "currency_pair": "EURUSD", "rate": 1.123242,
          "rate_details": [{"level": "CLIENT", "rate": 1.123242, "buy_amount": 1000.0, "sell_amount": 1123.24}]}
EURUSD_rev = {"sell_currency": "EUR", "buy_currency": "USD", "currency_pair": "EURUSD", "rate": 1.111559}
USDJPY = {"sell_currency": "USD", "buy_currency": "JPY", "currency_pair": "USDJPY", "rate": 150.0}

def test_rate_direction_follows_currency_pair_not_sell_buy():
    assert rate_to_usd(EURUSD, "EUR") == D("1.123242")
    assert rate_to_usd(EURUSD_rev, "EUR") == D("1.111559")      # still USD per EUR
    assert rate_to_usd(USDJPY, "JPY") == (D(1) / D("150")).quantize(D("1e-10"))

def test_quote_cost_prefers_reported_sell_amount():
    assert quote_cost({"sell_amount": 11.24, "client_rate": 1.124136, "currency_pair": "EURUSD",
                       "sell_currency": "USD", "buy_currency": "EUR"}, D("10")) == D("11.24")
    assert quote_cost({"client_rate": 1.124136, "currency_pair": "EURUSD", "sell_currency": "USD",
                       "buy_currency": "EUR"}, D("10")) == D("11.24")

def test_unknown_pair_raises():
    with pytest.raises(ValueError):
        rate_to_usd({"currency_pair": "GBPEUR", "rate": 1.1}, "GBP")

def test_quote_cost_without_pair_does_not_guess():
    assert quote_cost({"client_rate": 1.1, "sell_currency": "USD", "buy_currency": "EUR"}, D("10")) is None
