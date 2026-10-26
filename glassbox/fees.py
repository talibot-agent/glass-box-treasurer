"""Fee reconciliation. Airwallex reports the real fee on each transfer (`fee_amount`, `fee_currency`).
FeeBook keeps the highest fee seen per (currency, method) and lifts the policy's SWIFT fee to it (never lowers).
observe() returns True when the live fee exceeded the policy's base fee, which is a mismatch to audit."""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path


class FeeBook:
    def __init__(self, path):
        self.path = Path(path)
        self._d = json.loads(self.path.read_text()) if self.path.exists() else {}

    def max_fee(self, ccy, method):
        v = self._d.get(f"{ccy}:{method}")
        return Decimal(v) if v is not None else None

    def observe(self, ccy, method, fee: Decimal, policy) -> bool:
        fee = Decimal(str(fee)); k = f"{ccy}:{method}"
        if self._d.get(k) is None or fee > Decimal(self._d[k]):
            self._d[k] = str(fee); self.path.write_text(json.dumps(self._d, indent=1))
        base_ccy = policy.c("swift_fee")["fee"]["currency"]
        base = Decimal(str(policy.c("swift_fee")["fee"]["amount"]))
        return method == "SWIFT" and ccy == base_ccy and fee > base

    def policy(self, policy):
        f = policy.c("swift_fee")["fee"]
        seen = self.max_fee(f["currency"], "SWIFT")
        base = Decimal(str(f["amount"]))
        return policy.with_swift_fee(seen) if seen is not None and seen > base else policy
