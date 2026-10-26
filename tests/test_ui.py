import threading, urllib.request, urllib.parse, json
from decimal import Decimal as D
from glassbox.audit import AuditLog
from glassbox.approvals import ApprovalBook, binding
from glassbox.planner import Planner
from glassbox.policy import Policy
from glassbox.seed import OBLIGATIONS, SEED_BALANCES
from glassbox.tracking import TransferState
from glassbox import ui
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]

def world(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    p = Planner(Policy.load(ROOT / "policies/default.yaml"), OBLIGATIONS, {"EUR": D("1.08"), "GBP": D("1.27")}, log)
    p.plan(dict(SEED_BALANCES), 0.9)
    book = ApprovalBook(tmp_path / "ap.json")
    tok = book.request(binding(obligation_id="OB-X", action="TRANSFER", amount=D("10"), currency="EUR",
                               counterparty="<script>alert(1)</script>", evidence="e"))
    st = TransferState(tmp_path / "s.json", log); st.update("T1", "CANCELLED", "t", "INCORRECT_ROUTING", "returned")
    return ui.Dashboard(p, book, log, st), tok

def test_page_shows_plan_clauses_approvals_audit_and_escapes(tmp_path):
    d, tok = world(tmp_path); h = d.render()
    for ob in OBLIGATIONS: assert ob.id in h
    assert "C4" in h and "C5" in h and "all clauses pass" in h  # cited rules
    assert tok[:12] in h and "PENDING" in h            # approvals
    assert "chain valid" in h and "plan" in h          # audit
    assert "INCORRECT_ROUTING" in h                     # transfer outcomes
    assert "<script>alert" not in h and "&lt;script&gt;" in h
    assert "AI" in h                                   # authorship disclosure

def test_http_approve_flow(tmp_path):
    d, tok = world(tmp_path)
    srv = ui.make_server(d, 0); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert b"Glass Box" in urllib.request.urlopen(f"http://127.0.0.1:{port}/").read()
        j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state").read())
        assert j["audit_valid"] and j["plan"]
        data = urllib.parse.urlencode({"token": tok, "approver": "mykel"}).encode()
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/approve", data=data))
        assert r.status == 200 and d.book.all()[tok]["status"] == "APPROVED"
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/approve", data=data)); assert False
        except urllib.error.HTTPError as e: assert e.code == 409   # single-use: can't re-approve
    finally: srv.shutdown()
    assert srv.server_address[0] == "127.0.0.1"   # local only
