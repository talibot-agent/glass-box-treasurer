"""Real Airwallex sandbox client implementing AirwallexClient. Stdlib only; transport is injectable for tests.
Base URL: both api-demo.airwallex.com and api.sandbox.airwallex.com authenticated (tested 2026-10-26); default api-demo.
"""
from __future__ import annotations
import json, os, time, urllib.error, urllib.request, uuid
from decimal import Decimal
from pathlib import Path
from typing import Callable

DEFAULT_BASE = "https://api-demo.airwallex.com"
SECRETS = "/workspace/projects/_secrets/airwallex_sandbox.txt"

Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict]]


class AirwallexError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"HTTP {status}: {body.get('code')} {body.get('message')}")
        self.status, self.body = status, body


def urllib_transport(method, url, headers, data):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:200].decode(errors="replace")}


def load_creds(path: str = SECRETS) -> tuple[str, str]:
    cid, key = os.environ.get("AWX_CLIENT_ID"), os.environ.get("AWX_API_KEY")
    if cid and key:
        return cid, key
    kv = dict(l.strip().split("=", 1) for l in Path(path).read_text().splitlines() if "=" in l)
    return kv["client_id"], kv["api_key"]


def _dec(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(type(o))


class AirwallexHTTP:
    def __init__(self, client_id: str, api_key: str, base: str | None = None,
                 transport: Transport = urllib_transport, clock: Callable[[], float] = time.time):
        self._cid, self._key = client_id, api_key
        self.base = (base or os.environ.get("AWX_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self._t, self._clock = transport, clock
        self._token, self._exp = None, 0.0

    def __repr__(self):  # never leak secrets
        return f"AirwallexHTTP(base={self.base!r})"

    @classmethod
    def from_secrets(cls, **kw):
        return cls(*load_creds(), **kw)

    def login(self) -> None:
        st, body = self._t("POST", self.base + "/api/v1/authentication/login",
                           {"x-client-id": self._cid, "x-api-key": self._key, "Content-Type": "application/json"}, b"{}")
        if st >= 300 or "token" not in body:
            raise AirwallexError(st, body)
        self._token, self._exp = body["token"], self._clock() + 25 * 60  # tokens last 30 min

    def _req(self, method, path, payload=None, retry_auth=True):
        if not self._token or self._clock() >= self._exp:
            self.login()
        data = json.dumps(payload, default=_dec).encode() if payload is not None else None
        st, body = self._t(method, self.base + "/api/v1" + path,
                           {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}, data)
        if st == 401 and retry_auth:
            self._token = None
            return self._req(method, path, payload, retry_auth=False)
        if st >= 300:
            raise AirwallexError(st, body)
        return body

    # --- AirwallexClient interface ---
    def balances(self) -> dict[str, Decimal]:
        body = self._req("GET", "/balances/current")
        items = body if isinstance(body, list) else body.get("items", [])
        return {b["currency"]: Decimal(str(b.get("available_amount", 0))) for b in items}

    def fx_rate(self, sell, buy) -> Decimal:
        if sell == buy:
            return Decimal(1)
        body = self._req("GET", f"/fx/rates/current?sell_currency={sell}&buy_currency={buy}&buy_amount=1000")
        return Decimal(str(body["rate"]))

    def create_quote(self, sell, buy, buy_amount):
        body = self._req("POST", "/fx/quotes/create", {"sell_currency": sell, "buy_currency": buy,
                                                       "buy_amount": Decimal(buy_amount), "validity": "MIN_15"})
        return {"quote_id": body["quote_id"], "sell": sell, "buy": buy,
                "buy_amount": Decimal(str(buy_amount)), "rate": Decimal(str(body.get("client_rate", body.get("rate", 0))))}

    def create_conversion(self, quote_id, request_id):
        return self._req("POST", "/fx/conversions/create", {"quote_id": quote_id, "request_id": request_id})

    def create_transfer(self, beneficiary_id, currency, amount, request_id):
        return self._req("POST", "/transfers/create", {
            "beneficiary_id": beneficiary_id, "transfer_currency": currency, "transfer_amount": Decimal(amount),
            "source_currency": currency, "transfer_method": "LOCAL", "reference": "GlassBox", "reason": "payment_to_supplier",
            "request_id": request_id})

    def global_accounts(self) -> list[dict]:
        b = self._req("GET", "/global_accounts")
        return b.get("items", []) if isinstance(b, dict) else b

    def simulate_deposit(self, currency, amount, global_account_id: str | None = None):
        payload = {"amount": Decimal(amount), "currency": currency, "request_id": str(uuid.uuid4())}
        if global_account_id:
            payload["global_account_id"] = global_account_id
        return self._req("POST", "/simulation/deposit/create", payload)
