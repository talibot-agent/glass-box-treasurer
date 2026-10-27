"""Deterministic replay of the audit log: re-run the planner from the recorded inputs (plan balances/confidence/rates,
then each recalc trigger) into a scratch log, and require every decision-relevant entry (plan, recalc, decision) to be
byte-identical after canonicalisation. Also checks the hash chain and that the policy in force is the one recorded.
CLI: python3 -m glassbox.replay AUDIT.jsonl [POLICY.yaml]"""
from __future__ import annotations
import json, sys, tempfile
from decimal import Decimal
from pathlib import Path

from .audit import AuditLog, _canon
from .planner import Planner, policy_hash

DECISION_KINDS = ("plan", "recalc", "decision")


def _dec(d):
    return {k: Decimal(str(v)) for k, v in (d or {}).items()}


def _norm(entries):
    return [json.loads(_canon({"kind": e["kind"], "payload": e["payload"]})) for e in entries if e["kind"] in DECISION_KINDS]


def replay(audit_path, policy, obligations) -> dict:
    log = AuditLog(audit_path)
    entries = log.entries()
    diffs = [] if log.verify() else ["hash chain invalid"]
    scratch = AuditLog(Path(tempfile.mkdtemp()) / "replay.jsonl")
    p = None
    for e in entries:
        k, pl = e["kind"], e["payload"]
        if k == "plan":
            if pl.get("policy_hash") and pl["policy_hash"] != policy_hash(policy):
                diffs.append(f"seq {e['seq']}: policy changed since this plan was made")
            p = Planner(policy, obligations, _dec(pl["rates"]), scratch)
            p.plan(_dec(pl["balances"]), pl["confidence"])
        elif k == "recalc" and p is not None:
            t = pl.get("trigger")
            if t == "deposit":
                if "balances" not in pl:
                    diffs.append(f"seq {e['seq']}: deposit recalc has no balances; cannot replay"); continue
                p.on_deposit(pl["currency"], _dec(pl["balances"]))
            elif t == "confidence":
                p.update_confidence(pl["confidence"])
            elif t == "transfer_failed":
                p.on_transfer_failed(pl["obligation"], pl["transfer_id"], pl.get("failure_type"))
            else:
                diffs.append(f"seq {e['seq']}: unknown recalc trigger {t}")
    a, b = _norm(entries), _norm(scratch.entries())
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            diffs.append(f"decision entry {i} ({x['kind']}) differs"); break
    if len(a) != len(b):
        diffs.append(f"entry count {len(a)} recorded vs {len(b)} replayed")
    return {"ok": not diffs, "diffs": diffs, "replayed": len(b), "recorded": len(a)}


if __name__ == "__main__":  # pragma: no cover
    from .policy import Policy
    from .seed import OBLIGATIONS
    root = Path(__file__).resolve().parents[1]
    pol = Policy.load(sys.argv[2] if len(sys.argv) > 2 else root / "policies/default.yaml")
    r = replay(sys.argv[1], pol, OBLIGATIONS); print(json.dumps(r, indent=1)); sys.exit(0 if r["ok"] else 1)
