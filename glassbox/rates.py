"""FX rate normalisation, verified live 2026-10-26: Airwallex quotes `rate`/`client_rate` in the direction of
`currency_pair` (e.g. EURUSD = USD per 1 EUR) regardless of which side you sell. A USD->EUR and an EUR->USD check
both come back as EURUSD. Never infer direction from sell/buy."""
from __future__ import annotations
from decimal import Decimal

Q = Decimal("1e-10")


def _rate(body) -> Decimal:
    return Decimal(str(body.get("client_rate", body.get("rate"))))


def rate_to_usd(body: dict, ccy: str) -> Decimal:
    pair, r = body["currency_pair"], _rate(body)
    if pair == f"{ccy}USD":
        return r
    if pair == f"USD{ccy}":
        return (Decimal(1) / r).quantize(Q)
    raise ValueError(f"pair {pair} does not price {ccy} in USD")


def quote_cost(body: dict, buy_amount: Decimal) -> Decimal:
    """Sell-currency cost of buying buy_amount: the reported sell_amount if present, else derived from the pair."""
    if body.get("sell_amount") is not None:
        return Decimal(str(body["sell_amount"]))
    sell, buy, pair, r = body["sell_currency"], body["buy_currency"], body.get("currency_pair"), _rate(body)
    if not pair:
        return None  # direction unknown: do not guess
    if pair == buy + sell:        # price of buy in sell units
        return (Decimal(buy_amount) * r).quantize(Decimal("0.01"))
    if pair == sell + buy:
        return (Decimal(buy_amount) / r).quantize(Decimal("0.01"))
    raise ValueError(f"pair {pair} does not match {sell}/{buy}")


def live_rates_to_usd(client, currencies) -> dict[str, Decimal]:
    out = {}
    for c in currencies:
        if c != "USD":
            out[c] = rate_to_usd(client._req("GET", f"/fx/rates/current?sell_currency=USD&buy_currency={c}&buy_amount=1000"), c)
    return out
