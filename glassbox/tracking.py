"""Transfer status tracking to a terminal state. Per the builder guide: PAID is success; CANCELLED is the failure
terminal (read failure_type/failure_reason; it does not mean a person cancelled); SENT is in-flight, never final;
there is no FAILED transfer status. A poll budget that runs out returns final=False (unknown, not failed)."""
from __future__ import annotations
import time
from decimal import Decimal

TERMINAL = frozenset({"PAID", "CANCELLED"})


class TransferTracker:
    def __init__(self, client, audit, sleep=time.sleep, on_fee=None, interval=2.0):
        self.client, self.audit, self.sleep, self.on_fee, self.interval = client, audit, sleep, on_fee, interval

    def wait(self, transfer_id: str, max_polls: int = 30) -> dict:
        history, body = [], {}
        for i in range(max_polls):
            body = self.client.get_transfer(transfer_id)
            st = body.get("status")
            if not history or history[-1] != st:
                history.append(st)
                self.audit.append("transfer_status", {"id": transfer_id, "status": st,
                                                      "failure_type": body.get("failure_type")})
            if st in TERMINAL:
                break
            if i < max_polls - 1:
                self.sleep(min(self.interval * (1.5 ** i), 15))
        st = body.get("status"); final = st in TERMINAL
        if self.on_fee and body.get("fee_amount") is not None:
            self.on_fee(body.get("fee_currency"), body.get("transfer_method"), Decimal(str(body["fee_amount"])))
        outcome = "PAID" if st == "PAID" else "FAILED" if st == "CANCELLED" else "IN_FLIGHT"
        return {"id": transfer_id, "status": st, "final": final, "outcome": outcome, "history": history,
                "failure_type": body.get("failure_type"), "failure_reason": body.get("failure_reason")}
