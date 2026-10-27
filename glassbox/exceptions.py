"""Exception routing: a transfer that ends CANCELLED (the failure terminal; the sandbox lands FAILED as CANCELLED +
failure_type) escalates the obligation that paid it. Wire as TransferState(on_change=FailureRouter(...).on_state);
TransferState only fires on a real state change, so webhook replays and stale/out-of-order events never re-escalate."""
from __future__ import annotations


class FailureRouter:
    def __init__(self, planner, executor):
        self.planner, self.executor = planner, executor

    def on_state(self, state: dict):
        if state.get("outcome") != "FAILED":
            return None
        ob = self.executor.obligation_for_transfer(state["id"])
        if ob is None or ob not in self.planner.current:
            self.planner.audit.append("unmatched_failure", {"transfer_id": state["id"],
                                                            "failure_type": state.get("failure_type")})
            return None
        return self.planner.on_transfer_failed(ob, state["id"], state.get("failure_type"))
