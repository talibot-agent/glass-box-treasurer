"""Human-approval binding. An approval covers exactly one (obligation, action, amount, currency, counterparty,
evidence) tuple, is single-use, and persists to disk. Any change to any field yields a different digest, so an
approval can never be reused for a larger, redirected or re-evidenced payment."""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


class ApprovalMismatch(Exception):
    pass


@dataclass(frozen=True)
class Binding:
    obligation_id: str
    action: str
    amount: str
    currency: str
    counterparty: str
    evidence_hash: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


def binding(*, obligation_id, action, amount: Decimal, currency, counterparty, evidence) -> Binding:
    amt = format(Decimal(amount).quantize(Decimal("0.01")), "f")
    ev = hashlib.sha256(json.dumps(sorted(evidence)).encode()).hexdigest()
    return Binding(obligation_id, str(getattr(action, "value", action)), amt, currency, counterparty, ev)


class ApprovalBook:
    def __init__(self, path):
        self.path = Path(path)
        self._d = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        self.path.write_text(json.dumps(self._d, indent=1, sort_keys=True))

    def request(self, b: Binding) -> str:
        self._d.setdefault(b.digest, {"binding": b.__dict__, "status": "PENDING", "requested_at": time.time()})
        self._save()
        return b.digest

    def approve(self, token: str, approver: str):
        if token not in self._d or self._d[token]["status"] != "PENDING":
            raise ApprovalMismatch("no pending approval for token")
        self._d[token].update(status="APPROVED", approver=approver, approved_at=time.time())
        self._save()

    def all(self) -> dict:
        return json.loads(json.dumps(self._d))

    def status(self, b: Binding) -> str | None:
        e = self._d.get(b.digest)
        return e and e["status"]

    def consume(self, b: Binding) -> dict:
        e = self._d.get(b.digest)
        if not e or e["status"] != "APPROVED":
            raise ApprovalMismatch(f"no unused approval matching {b}")
        e["status"] = "USED"; e["used_at"] = time.time()
        self._save()
        return e
