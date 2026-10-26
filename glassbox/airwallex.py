"""Airwallex client interface + in-memory fake. The real HTTP client is wired after sandbox signup.

Gotchas encoded: amounts in MAJOR units; request_id is a UUID reused only for retries (idempotent);
FX quotes are single-use; no x-api-version on FX calls.
"""
from __future__ import annotations
import uuid
from decimal import Decimal
from typing import Protocol


class AirwallexClient(Protocol):
    def balances(self) -> dict[str, Decimal]: ...
    def fx_rate(self, sell: str, buy: str) -> Decimal: ...
    def create_quote(self, sell: str, buy: str, buy_amount: Decimal) -> dict: ...
    def create_conversion(self, quote_id: str, request_id: str) -> dict: ...
    def create_transfer(self, beneficiary_id: str, currency: str, amount: Decimal, request_id: str, method: str = "LOCAL") -> dict: ...
    def simulate_deposit(self, currency: str, amount: Decimal) -> dict: ...


class QuoteAlreadyUsed(Exception):
    pass


class InsufficientFunds(Exception):
    pass


class FakeAirwallex:
    """Deterministic sandbox stand-in for tests and offline demo."""

    def __init__(self, balances: dict[str, Decimal], rates: dict[tuple[str, str], Decimal]):
        self._bal = {k: Decimal(v) for k, v in balances.items()}
        self._rates = rates
        self._quotes: dict[str, dict] = {}
        self._by_request: dict[str, dict] = {}
        self.transfers: list[dict] = []

    def balances(self):
        return dict(self._bal)

    def fx_rate(self, sell, buy):
        return Decimal(1) if sell == buy else self._rates[(sell, buy)]

    def create_quote(self, sell, buy, buy_amount):
        q = {"quote_id": f"Q-{uuid.uuid4().hex[:8]}", "sell": sell, "buy": buy,
             "buy_amount": Decimal(buy_amount), "rate": self.fx_rate(sell, buy), "used": False}
        self._quotes[q["quote_id"]] = q
        return {k: v for k, v in q.items() if k != "used"}

    def create_conversion(self, quote_id, request_id):
        if request_id in self._by_request:
            return self._by_request[request_id]
        q = self._quotes[quote_id]
        if q["used"]:
            raise QuoteAlreadyUsed(quote_id)
        sell_amt = (q["buy_amount"] / q["rate"]).quantize(Decimal("0.01"))
        if self._bal.get(q["sell"], Decimal(0)) < sell_amt:
            raise InsufficientFunds(q["sell"])
        q["used"] = True
        self._bal[q["sell"]] -= sell_amt
        self._bal[q["buy"]] = self._bal.get(q["buy"], Decimal(0)) + q["buy_amount"]
        res = {"conversion_id": f"CV-{uuid.uuid4().hex[:8]}", "quote_id": quote_id, "sell_amount": sell_amt}
        self._by_request[request_id] = res
        return res

    def create_transfer(self, beneficiary_id, currency, amount, request_id, method="LOCAL"):
        if request_id in self._by_request:
            return self._by_request[request_id]
        amount = Decimal(amount)
        if self._bal.get(currency, Decimal(0)) < amount:
            raise InsufficientFunds(currency)
        self._bal[currency] -= amount
        res = {"transfer_id": f"T-{uuid.uuid4().hex[:8]}", "status": "PROCESSING",
               "beneficiary_id": beneficiary_id, "currency": currency, "amount": amount,
               "method": method, "request_id": request_id}
        self.transfers.append(res)
        self._by_request[request_id] = res
        return res

    def simulate_deposit(self, currency, amount):
        self._bal[currency] = self._bal.get(currency, Decimal(0)) + Decimal(amount)
        return {"status": "SETTLED", "currency": currency, "amount": Decimal(amount)}
