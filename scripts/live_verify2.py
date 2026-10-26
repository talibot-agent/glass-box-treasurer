"""Live check #2 (sandbox funds only): live rates into the planner, quote cost vs settled conversion,
transfer simulated SENT -> PAID tracked to terminal with fee reconciliation, local signed webhook round trip."""
import sys, json, pathlib, uuid, time, threading, hmac, hashlib, urllib.request
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from decimal import Decimal as D
from glassbox.http_client import AirwallexHTTP, AirwallexError
from glassbox.rates import live_rates_to_usd
from glassbox.planner import Planner
from glassbox.policy import Policy
from glassbox.seed import OBLIGATIONS, SEED_BALANCES
from glassbox.audit import AuditLog
from glassbox.fees import FeeBook
from glassbox.tracking import TransferTracker
from glassbox.webhook import WebhookReceiver, serve

out = root / "out"; out.mkdir(exist_ok=True); R = {}
c = AirwallexHTTP.from_secrets(); audit = AuditLog(out / "live2_audit.jsonl")
pol = FeeBook(out / "fees.json").policy(Policy.load(root / "policies/default.yaml"))
rates = live_rates_to_usd(c, ["EUR", "GBP"]); R["rates_to_usd"] = {k: str(v) for k, v in rates.items()}
plan = Planner(pol, OBLIGATIONS, rates, audit).plan(dict(SEED_BALANCES), 0.9)
R["plan_live_rates"] = {k: [v.action.value, v.verdict.value, str(v.proposal.buy_amount or "")] for k, v in sorted(plan.items())}
q = c.create_quote("USD", "EUR", D("10"))
conv = c.create_conversion(q["quote_id"], str(uuid.uuid4()))
R["quote_vs_conversion"] = {"pair": q["currency_pair"], "client_rate": str(q["rate"]), "predicted_sell": str(q["sell_cost"]),
                            "settled_sell": str(conv.get("sell_amount")), "status": conv.get("status")}
tid = "23fc820d-c509-4280-ae02-c352f8acf94c"   # 10 EUR SWIFT transfer from live_verify #1
for st in ("SENT", "PAID"):
    try: c.simulate_transfer(tid, st); R[f"sim_{st}"] = "ok"
    except AirwallexError as e: R[f"sim_{st}"] = f"{e.status} {e.body.get('code')} {e.body.get('message')}"
    time.sleep(1)
fb = FeeBook(out / "fees.json")
R["track"] = TransferTracker(c, audit, on_fee=lambda cc, m, f: R.setdefault("fee_mismatch", fb.observe(cc, m, f, pol)), interval=1).wait(tid, max_polls=8)
got = []; secret = uuid.uuid4().hex
rx = WebhookReceiver(secret, out / "wh_seen.json", audit, on_event=got.append)
threading.Thread(target=serve, args=(rx, 8799), daemon=True).start(); time.sleep(0.5)
body = json.dumps({"id": f"evt_{uuid.uuid4().hex[:8]}", "name": "payout.transfer.paid", "data": {"object": {"id": tid, "status": "PAID"}}}).encode()
ts = str(int(time.time() * 1000)); sig = hmac.new(secret.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
def post(sig):
    r = urllib.request.Request("http://127.0.0.1:8799/", data=body, method="POST", headers={"x-timestamp": ts, "x-signature": sig})
    try: return urllib.request.urlopen(r).status
    except urllib.error.HTTPError as e: return e.code
R["webhook"] = {"valid": post(sig), "replay": post(sig), "tampered": post("0" * 64), "delivered_once": len(got) == 1}
R["audit_valid"] = audit.verify()
(out / "live_verify2.json").write_text(json.dumps(R, indent=1, default=str)); print(json.dumps(R, indent=1, default=str))
