"""Live: fresh 10 EUR SWIFT transfer -> simulate SENT -> CANCELLED(failure_type); poll tracker + real webhooks."""
import sys, json, time, uuid, pathlib
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from glassbox.http_client import AirwallexHTTP, AirwallexError
from glassbox.audit import AuditLog
from glassbox.tracking import TransferTracker, TransferState
c = AirwallexHTTP.from_secrets(); out = root / "out"
audit = AuditLog(out / "live3_audit.jsonl"); state = TransferState(out / "live3_state.json", audit)
t = c.create_transfer("ea0e6209-ce9f-4d93-be7d-3887ba69f98f", "EUR", 10, str(uuid.uuid4()), method="SWIFT")
tid = t["id"]; print("transfer", tid, t.get("status"))
for st, ft in [("SENT", None), ("CANCELLED", "RETURNED_BY_BENEFICIARY_BANK")]:
    time.sleep(3)
    try: c.simulate_transfer(tid, st, ft); print("sim", st, "ok")
    except AirwallexError as e: print("sim", st, "ERR", e.status, json.dumps(e.body)[:300])
r = TransferTracker(c, audit, state=state, interval=3).wait(tid, max_polls=10)
print(json.dumps({k: r[k] for k in ("status", "final", "outcome", "failure_type", "failure_reason", "history")}))
time.sleep(20)
print("webhook state:", json.dumps(json.loads((out / "transfer_state.json").read_text()).get(tid)) if (out / "transfer_state.json").exists() else None)
print("audit chain valid:", audit.verify())
