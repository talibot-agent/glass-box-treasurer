"""Executor: turns an approved PlanItem into Airwallex calls, exactly once.
- Only CONVERT (fresh single-use quote, then conversion) and FUND (transfer) execute; DEFER/ESCALATE/REJECT never do.
- NEEDS_HUMAN executes only by consuming an approval bound to the exact amount/currency/counterparty/evidence.
- request_id = uuid5(op key), so a retry after a lost response reuses it (idempotent) and a new op never does.
- Persistent ledger is the duplicate lock: a DONE op is never sent again, even after restart."""
from __future__ import annotations
import json, uuid
from decimal import Decimal
from pathlib import Path

from .approvals import ApprovalBook, ApprovalMismatch, binding
from .audit import AuditLog
from .models import Action, Obligation, Verdict

NS = uuid.UUID("6b1f3c2e-9a57-4f0e-8d2b-0c5e7a9b1d44")


class ExecutionRefused(Exception):
    pass


class Executor:
    def __init__(self, client, audit: AuditLog, approvals: ApprovalBook, ledger_path):
        self.client, self.audit, self.approvals = client, audit, approvals
        self.path = Path(ledger_path)
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        self.path.write_text(json.dumps(self.ledger, indent=1, sort_keys=True, default=str))

    def binding_for(self, item, ob: Obligation):
        p = item.proposal
        if item.action == Action.CONVERT:
            amount, ccy = p.buy_amount, p.buy_currency
        else:
            amount, ccy = ob.amount, ob.currency
        return binding(obligation_id=ob.id, action=item.action, amount=amount, currency=ccy,
                       counterparty=ob.counterparty, evidence=p.evidence)

    def execute(self, item, ob: Obligation, beneficiary_id: str | None = None) -> dict:
        if item.action not in (Action.CONVERT, Action.FUND) or item.verdict == Verdict.REJECT:
            raise ExecutionRefused(f"{item.action.value}/{item.verdict.value} is never executed")
        if item.action == Action.FUND and not beneficiary_id:
            raise ExecutionRefused("FUND requires a created beneficiary_id")
        b = self.binding_for(item, ob)
        key = f"{item.action.value}:{ob.id}:{b.digest}"
        rec = self.ledger.get(key)
        if rec and rec["status"] == "DONE":
            self.audit.append("execute_skipped", {"key": key, "reason": "duplicate lock"})
            return {**rec, "status": "ALREADY_DONE"}
        if item.verdict == Verdict.NEEDS_HUMAN and not rec:  # a STARTED op already consumed its approval
            try:
                ap = self.approvals.consume(b)
            except ApprovalMismatch as e:
                self.audit.append("execute_refused", {"key": key, "reason": str(e)})
                raise ExecutionRefused(str(e)) from e
            approver = ap.get("approver")
        else:
            approver = rec.get("approver") if rec else None
        rid = str(uuid.uuid5(NS, key))
        self.ledger[key] = rec = {"status": "STARTED", "request_id": rid, "approver": approver}
        self._save()
        if item.action == Action.CONVERT:
            p = item.proposal
            q = self.client.create_quote(p.sell_currency, p.buy_currency, p.buy_amount)
            res = self.client.create_conversion(q["quote_id"], rid)
            res = {"quote_id": q["quote_id"], "rate": q["rate"], **res}
        else:
            res = self.client.create_transfer(beneficiary_id, ob.currency, ob.amount, rid, method=ob.method)
        rec.update(status="DONE", result=res)
        self._save()
        self.audit.append("execute", {"key": key, "request_id": rid, "binding": b.__dict__, "approver": approver,
                                      "verdict": item.verdict.value, "result": res})
        return {"status": "DONE", **rec}
