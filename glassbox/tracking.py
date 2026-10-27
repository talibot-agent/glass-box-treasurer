"""Transfer status tracking to a terminal state. Per the builder guide: PAID is success; CANCELLED is the failure
terminal (read failure_type/failure_reason; it does not mean a person cancelled); SENT is in-flight, never final;
there is no FAILED transfer status. A poll budget that runs out returns final=False (unknown, not failed)."""
from __future__ import annotations
import time
from decimal import Decimal

TERMINAL = frozenset({"PAID", "CANCELLED"})


class TransferTracker:
    def __init__(self, client, audit, sleep=time.sleep, on_fee=None, interval=2.0, state=None):
        self.client, self.audit, self.sleep, self.on_fee, self.interval = client, audit, sleep, on_fee, interval
        self.state = state

    def wait(self, transfer_id: str, max_polls: int = 30) -> dict:
        history, body = [], {}
        for i in range(max_polls):
            body = self.client.get_transfer(transfer_id)
            st = body.get("status")
            if not history or history[-1] != st:
                history.append(st)
                self.audit.append("transfer_status", {"id": transfer_id, "status": st,
                                                      "failure_type": failure_of(body)[0]})
            if st in TERMINAL:
                break
            if i < max_polls - 1:
                self.sleep(min(self.interval * (1.5 ** i), 15))
        st = body.get("status"); final = st in TERMINAL
        if self.state is not None:
            self.state.update(transfer_id, st, body.get("updated_at", ""), *failure_of(body))
        if self.on_fee and body.get("fee_amount") is not None:
            self.on_fee(body.get("fee_currency"), body.get("transfer_method"), Decimal(str(body["fee_amount"])))
        outcome = "PAID" if st == "PAID" else "FAILED" if st == "CANCELLED" else "IN_FLIGHT"
        return {"id": transfer_id, "status": st, "final": final, "outcome": outcome, "history": history,
                "failure_type": failure_of(body)[0], "failure_reason": failure_of(body)[1]}


class TransferState:
    """Persisted latest-known state per transfer, fed by webhooks and polling. Events can arrive out of order:
    a terminal state is sticky, and an older event never overwrites a newer one (ordered by created_at)."""

    def __init__(self, path, audit, on_change=None):
        import json
        from pathlib import Path
        self._json, self.path, self.audit, self.on_change = json, Path(path), audit, on_change
        self._d = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, tid):
        return self._d.get(tid)

    def all(self):
        return dict(self._d)

    def update(self, tid, status, at="", failure_type=None, failure_reason=None, source="poll"):
        cur = self._d.get(tid)
        if cur and (cur["final"] or (at and cur.get("at") and at < cur["at"])):
            return cur
        final = status in TERMINAL
        new = {"id": tid, "status": status, "final": final, "at": at, "source": source,
               "outcome": "PAID" if status == "PAID" else "FAILED" if status == "CANCELLED" else "IN_FLIGHT",
               "failure_type": failure_type, "failure_reason": failure_reason}
        self._d[tid] = new
        self.path.write_text(self._json.dumps(self._d, indent=1))
        self.audit.append("transfer_state", new)
        if self.on_change:  # only on a real state change: duplicates/stale/after-terminal events never re-fire
            self.on_change(new)
        return new

    def apply_event(self, evt):
        name = evt.get("name") or ""
        if not name.startswith("payout.transfer."):
            return None
        obj = (evt.get("data") or {}).get("object") or {}
        status = obj.get("status") or name.rsplit(".", 1)[-1].upper()
        return self.update(obj.get("id"), status, evt.get("created_at", ""), *failure_of(obj), source=f"webhook:{evt.get('id')}")


def failure_of(body):
    """(type, reason). Live sandbox puts it in failure{code, details.type, message}; also accept flat fields."""
    f = body.get("failure") or {}
    if f:
        msg = f.get("message") or ""
        return (f.get("details") or {}).get("type") or body.get("failure_type"), (f"{msg} ({f['code']})" if f.get("code") else msg) or None
    return body.get("failure_type"), body.get("failure_reason")
