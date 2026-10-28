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
    def __init__(self, client, audit: AuditLog, approvals: ApprovalBook, ledger_path, fees=None, policy=None, ns: uuid.UUID = NS):
        self.client, self.audit, self.approvals = client, audit, approvals
        self.ns = ns  # request_id namespace; a live demo run passes its own so runs never collide server-side
        self.fees, self.policy = fees, policy
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

    def transfers_for(self, obligation_id: str) -> list[str]:
        return [r["result"]["transfer_id"] for r in self.ledger.values()
                if r.get("obligation") == obligation_id and (r.get("result") or {}).get("transfer_id")]

    def obligation_for_transfer(self, transfer_id: str) -> str | None:
        return next((r.get("obligation") for r in self.ledger.values()
                     if (r.get("result") or {}).get("transfer_id") == transfer_id), None)

    def execute(self, item, ob: Obligation, beneficiary_id: str | None = None) -> dict:
        """CONVERT with a beneficiary_id also pays the obligation after the conversion (a second, separately
        idempotent ledger op `PAY:`), so the Kit 1 supplier flow is quote -> conversion -> transfer."""
        if item.action not in (Action.CONVERT, Action.FUND) or item.verdict == Verdict.REJECT:
            raise ExecutionRefused(f"{item.action.value}/{item.verdict.value} is never executed")
        if item.action == Action.FUND and not beneficiary_id:
            raise ExecutionRefused("FUND requires a created beneficiary_id")
        b = self.binding_for(item, ob)
        key = f"{item.action.value}:{ob.id}:{b.digest}"
        pay_key = f"PAY:{ob.id}:{b.digest}" if item.action == Action.CONVERT and beneficiary_id else None
        rec = self.ledger.get(key)
        main_done = bool(rec and rec["status"] == "DONE")
        if main_done and (not pay_key or (self.ledger.get(pay_key) or {}).get("status") == "DONE"):
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
        if not main_done:
            rid = str(uuid.uuid5(self.ns, key))
            rec = self.ledger.setdefault(key, {})
            rec.update(status="STARTED", request_id=rid, approver=approver, obligation=ob.id)
            self._save()
            if item.action == Action.CONVERT:
                p = item.proposal
                if not rec.get("quote"):
                    # quotes are single-use: persist it BEFORE converting so a retry after a lost response reuses
                    # the same quote with the same request_id (server dedupes) instead of burning a new quote.
                    q = self.client.create_quote(p.sell_currency, p.buy_currency, p.buy_amount)
                    rec["quote"] = {"quote_id": q["quote_id"], "rate": str(q["rate"])}
                    self._save()
                q = rec["quote"]
                res = self.client.create_conversion(q["quote_id"], rid)
                res = {"quote_id": q["quote_id"], "rate": q["rate"], **res}
            else:
                res = self._transfer(ob, beneficiary_id, rid)
            rec.update(status="DONE", result=res)
            self._save()
            self.audit.append("execute", {"key": key, "request_id": rid, "binding": b.__dict__, "approver": approver,
                                          "verdict": item.verdict.value, "result": res})
        if pay_key:
            prec = self.ledger.setdefault(pay_key, {})
            prid = str(uuid.uuid5(self.ns, pay_key))
            prec.update(status="STARTED", request_id=prid, approver=approver, obligation=ob.id)
            self._save()
            pres = self._transfer(ob, beneficiary_id, prid)
            prec.update(status="DONE", result=pres)
            self._save()
            self.audit.append("execute", {"key": pay_key, "request_id": prid, "binding": b.__dict__,
                                          "approver": approver, "verdict": item.verdict.value, "result": pres})
        return {"status": "DONE", **rec}

    def _transfer(self, ob: Obligation, beneficiary_id: str, rid: str) -> dict:
        res = self.client.create_transfer(beneficiary_id, ob.currency, ob.amount, rid, method=ob.method)
        if self.fees is not None and res.get("fee_amount") is not None:
            fee, fccy = Decimal(str(res["fee_amount"])), res.get("fee_currency", ob.currency)
            if self.fees.observe(fccy, ob.method, fee, self.policy):
                self.audit.append("fee_mismatch", {"obligation": ob.id, "reported": fee, "currency": fccy,
                                                   "policy_base": self.policy.c("swift_fee")["fee"]["amount"]})
        return res
