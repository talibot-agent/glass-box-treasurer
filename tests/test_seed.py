from decimal import Decimal as D
from glassbox.seed import OBLIGATIONS, SEED_BALANCES
from glassbox.policy import Policy
from pathlib import Path

POL = Policy.load(Path(__file__).parents[1] / "policies/default.yaml")
RATES = {"EUR": D("1.08"), "GBP": D("1.27")}


def test_five_obligations_three_currencies_within_72h():
    assert len(OBLIGATIONS) == 5
    assert {o.currency for o in OBLIGATIONS} == {"USD", "EUR", "GBP"}
    assert all(o.due_hours <= 72 for o in OBLIGATIONS)
    assert {o.category for o in OBLIGATIONS} >= {"shipping", "payroll", "saas"}


def test_cash_covers_only_part_after_floors():
    floors = POL.c("reserve_floor")["floors"]
    usd = lambda a, c: a if c == "USD" else a * RATES[c]
    spendable = sum(usd(SEED_BALANCES[c] - D(str(floors[c])), c) for c in SEED_BALANCES)
    owed = sum(usd(o.amount, o.currency) for o in OBLIGATIONS)
    assert 0 < spendable < owed
