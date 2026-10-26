"""Live: CANCELLED (BENEFICIARY_BANK_RETURNED) on the already-SENT transfer from live_verify3, tracked to terminal."""
import sys, json, pathlib
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from glassbox.http_client import AirwallexHTTP
from glassbox.audit import AuditLog
from glassbox.tracking import TransferTracker, TransferState
tid = sys.argv[1]; c = AirwallexHTTP.from_secrets(); out = root / "out"
audit = AuditLog(out / "live3_audit.jsonl"); state = TransferState(out / "live3_state.json", audit)
# SENT->CANCELLED is rejected (400 bad failure_type / 500); simulate next_status=FAILED -> live status CANCELLED
if len(sys.argv) > 2: c.simulate_transfer(tid, "FAILED", "BENEFICIARY_BANK_RETURNED")
r = TransferTracker(c, audit, state=state, interval=3).wait(tid, max_polls=10)
print(json.dumps({k: r[k] for k in ("status", "final", "outcome", "failure_type", "failure_reason", "history")}))
print("state:", json.dumps(state.get(tid))); print("audit chain valid:", audit.verify())
