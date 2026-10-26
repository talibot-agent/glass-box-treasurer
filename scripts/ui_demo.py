"""Run the dashboard on 127.0.0.1:8800 with the offline seed plan, a pending approval and any live transfer states.
python3 scripts/ui_demo.py"""
import sys, pathlib
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from decimal import Decimal as D
from glassbox.audit import AuditLog
from glassbox.approvals import ApprovalBook, binding
from glassbox.planner import Planner
from glassbox.policy import Policy
from glassbox.seed import OBLIGATIONS, SEED_BALANCES
from glassbox.tracking import TransferState
from glassbox import ui
out = root / "out" / "ui"; out.mkdir(parents=True, exist_ok=True)
log = AuditLog(out / "audit.jsonl")
p = Planner(Policy.load(root / "policies/default.yaml"), OBLIGATIONS, {"EUR": D("1.08"), "GBP": D("1.27")}, log)
p.plan(dict(SEED_BALANCES), 0.9)
book = ApprovalBook(out / "approvals.json")
for k, it in p.current.items():
    if it.verdict.value == "NEEDS_HUMAN":
        ob = next(o for o in OBLIGATIONS if o.id == k)
        book.request(binding(obligation_id=k, action=it.action.value, amount=ob.amount,
                             currency=ob.currency, counterparty=ob.counterparty, evidence=log.head()))
src = root / "out" / "live3_state.json"
st = TransferState(out / "transfers.json", log)
if src.exists():
    import json
    for t in json.loads(src.read_text()).values(): st.update(t["id"], t["status"], t["at"], t["failure_type"], t["failure_reason"], t["source"])
srv = ui.make_server(ui.Dashboard(p, book, log, st), 8800); print("http://127.0.0.1:8800"); srv.serve_forever()
