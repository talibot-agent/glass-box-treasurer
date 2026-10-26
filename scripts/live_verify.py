"""Live sandbox verification with small amounts (sandbox money only). Prints no secrets.
python3 scripts/live_verify.py  -> writes out/live_verify.json"""
import sys, json, pathlib, uuid, time
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from decimal import Decimal as D
from glassbox.http_client import AirwallexHTTP, AirwallexError
from glassbox.beneficiaries import build_beneficiary
from glassbox.audit import AuditLog
from glassbox.approvals import ApprovalBook
from glassbox.executor import Executor
from glassbox.models import Action, Obligation, Proposal, Verdict
from glassbox.planner import PlanItem

out = root / "out"; out.mkdir(exist_ok=True)
c = AirwallexHTTP.from_secrets(); R = {}
def step(name, fn):
    try: R[name] = {"ok": True, "result": fn()}
    except AirwallexError as e: R[name] = {"ok": False, "status": e.status, "code": e.body.get("code"), "message": e.body.get("message")}
    print(name, "OK" if R[name]["ok"] else f"FAIL {R[name]}"); time.sleep(0.3)

before = c.balances(); R["balances_before"] = {k: str(v) for k, v in before.items()}
ex = Executor(c, AuditLog(out / "live_audit.jsonl"), ApprovalBook(out / "live_approvals.json"), out / "live_ledger.json")
SUPP = Obligation("LV-SUPP", "Lyon Textiles Demo", "supplier", "EUR", D("10"), 36, method="SWIFT")
tag = uuid.uuid4().hex[:6]
conv = PlanItem(SUPP.id, Action.CONVERT, Proposal(SUPP.id, Action.CONVERT, "USD", "EUR", D("10"), (f"live_verify:{tag}",)), Verdict.APPROVE, [], [])
step("conversion_10_EUR", lambda: {k: str(v) for k, v in ex.execute(conv, SUPP)["result"].items() if k in ("quote_id", "rate", "status", "conversion_id", "id", "buy_amount", "sell_amount")})
step("conversion_duplicate_lock", lambda: ex.execute(conv, SUPP)["status"])
ga = [g for g in c.global_accounts()]; R["global_accounts"] = [(g.get("id", "")[:8], g.get("status")) for g in ga]
step("deposit_10_USD", lambda: {k: str(v) for k, v in c.simulate_deposit("USD", D("10"), ga[0]["id"] if ga else None).items() if k in ("status", "amount", "currency", "id")})
us = build_beneficiary("US", name="Maersk Demo Inc", account_number="123456789", routing="021000021",
                       address={"street_address": "1 Demo St", "city": "New York", "state": "NY", "postcode": "10001"})
de = build_beneficiary("DE", name="Lyon Textiles Demo", iban="DE89370400440532013000", swift_code="COBADEFFXXX", bank_name="Commerzbank",
                       address={"street_address": "1 Demostr", "city": "Berlin", "postcode": "10115"})
step("schema_DE_SWIFT", lambda: len(c.beneficiary_schema("DE", "EUR", "SWIFT")["schema"]))
step("validate_US", lambda: c.validate_beneficiary(us)); step("validate_DE", lambda: c.validate_beneficiary(de))
step("create_US", lambda: c.create_beneficiary(us)); step("create_DE", lambda: c.create_beneficiary(de))
if R["create_DE"]["ok"]:
    fund = PlanItem(SUPP.id, Action.FUND, Proposal(SUPP.id, Action.FUND, evidence=(f"live_verify:{tag}",)), Verdict.APPROVE, [], [])
    step("transfer_10_EUR_SWIFT", lambda: {k: str(v) for k, v in ex.execute(fund, SUPP, beneficiary_id=R["create_DE"]["result"])["result"].items() if k in ("id", "status", "transfer_amount", "transfer_currency", "fee_amount", "amount_payer_pays")})
after = c.balances(); R["balances_after"] = {k: str(v) for k, v in after.items()}
R["delta"] = {k: str(after.get(k, 0) - before.get(k, 0)) for k in set(before) | set(after)}
(out / "live_verify.json").write_text(json.dumps(R, indent=1, default=str)); print(json.dumps(R["delta"]))
