"""Offline Kit 1 story with the deterministic planner (no network). python3 scripts/demo_offline.py"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from decimal import Decimal as D
from glassbox.audit import AuditLog
from glassbox.planner import Planner
from glassbox.policy import Policy
from glassbox.seed import OBLIGATIONS, SEED_BALANCES

root = pathlib.Path(__file__).resolve().parents[1]
log = AuditLog(pathlib.Path(tempfile.mkdtemp()) / "audit.jsonl")
p = Planner(Policy.load(root / "policies/default.yaml"), OBLIGATIONS, {"EUR": D("1.08"), "GBP": D("1.27")}, log)
show = lambda t: (print(f"\n== {t}"), [print(f"  {k:8} {v.action.value:8} {v.verdict.value:11} {','.join(v.clauses)}") for k, v in sorted(p.current.items())])
p.plan(dict(SEED_BALANCES), 0.9); show("Initial plan (confidence 0.90)")
print("  ->", p.update_confidence(0.55)); show("Customer email contradicts forecast (0.55)")
print("  ->", p.update_confidence(0.9))
bal = dict(SEED_BALANCES); bal["USD"] += D("5000")
print("\n  deposit USD 5000 ->", p.on_deposit("USD", bal)); show("After deposit")
print("\nprojected:", {k: str(v) for k, v in p.projected_balances(bal).items()})
print("audit entries:", len(log.entries()), "chain valid:", log.verify())
