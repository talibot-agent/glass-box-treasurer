"""S20: live Airwallex sandbox end-to-end run of the Kit 1 flow (sandbox money only; prints no secrets).
Agent view: the Kit 1 treasury (seed balances + 5 obligations) is the operating budget the policy engine plans against;
every executed action is a real sandbox call, so the sandbox wallet moves by exactly the executed outflows.

Flow: MCP get_balances -> live FX rates -> verify-and-revise loop (naive + planner, best-of-N) -> only the verified
winner executes (REST quote + REST conversion, REST transfers; NEEDS_HUMAN waits for a bound approval) -> duplicate
re-run creates nothing -> MCP simulate_transfer_result SENT->PAID, polled to terminal -> MCP simulate_create_deposit ->
planner recalculates only affected obligations -> MCP get_balances -> audit chain verify + replay of plan entries."""
from __future__ import annotations
import json, tempfile, time, uuid
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Beneficiaries created and validated live on 2026-10-26 (scripts/live_verify.py): US LOCAL aba/Checking, DE SWIFT.
BENEFICIARIES = {"US": "97e6ab20-e812-4c51-9637-852ee6d41f0a", "DE": "ea0e6209-ce9f-4d93-be7d-3887ba69f98f"}


def _bal(items):
    items = items if isinstance(items, list) else items.get("items", [])
    return {b["currency"]: D(str(b.get("available_amount", 0))) for b in items if b["currency"] in ("USD", "EUR", "GBP")}


def simulate_paid(mcp, c, tid, sleep=time.sleep) -> list[dict]:
    """Drive a sandbox transfer SENT -> PAID via the Developer MCP, falling back to REST once per step.
    Returns the failures (empty on success); stops at the first step neither path can do."""
    from .http_client import AirwallexError
    from .mcp_client import MCPError
    for st in ("SENT", "PAID"):
        sleep(1.5)
        try:
            mcp.call("simulate_transfer_result", {"transfer_id": tid, "next_status": st})
        except MCPError as e:
            try:
                c.simulate_transfer(tid, st)
            except AirwallexError as e2:
                return [{"transfer": tid, "status": st, "mcp": str(e)[:160], "rest": f"{e2.status} {e2.body.get('code')}"}]
    return []


def expected_delta(ledger: dict, deposit_usd: D) -> dict:
    """Sandbox balance change implied by the executed ledger: conversions (buy EUR, sell USD), transfer debits, deposit."""
    exp = {"USD": D(deposit_usd), "EUR": D(0), "GBP": D(0)}
    for k, r in ledger.items():
        res = r.get("result") or {}
        if r.get("status") != "DONE":
            continue
        if k.startswith("CONVERT:"):
            exp[res.get("buy_currency", "EUR")] += D(str(res.get("buy_amount", 0)))
            exp[res.get("sell_currency", "USD")] -= D(str(res.get("sell_amount", 0)))
        elif k.split(":")[0] in ("FUND", "PAY"):
            ccy = res.get("source_currency") or res.get("transfer_currency") or res.get("currency")
            exp[ccy] -= D(str(res.get("amount_payer_pays") or res.get("transfer_amount") or res.get("amount") or 0))
    return exp


def run(client=None, mcp=None, out_dir=None, confidence=0.9, deposit_usd=D("2500"), sleep=time.sleep,
        probe_quote_reuse=False) -> dict:
    from .approvals import ApprovalBook
    from .audit import AuditLog
    from .executor import Executor
    from .http_client import AirwallexHTTP, AirwallexError
    from .planner import Planner
    from .policy import Policy
    from .proposers import make_proposer
    from .rates import live_rates_to_usd
    from .replay import replay
    from .seed import OBLIGATIONS, SEED_BALANCES
    from .tracking import TransferTracker
    from .verify_loop import PlanLoop
    own_mcp = mcp is None
    if own_mcp:
        from .mcp_client import DeveloperMCP
        mcp = DeveloperMCP()
    c = client or AirwallexHTTP.from_secrets()
    out = Path(out_dir or tempfile.mkdtemp(prefix="s20-")); out.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    log = AuditLog(out / "audit.jsonl"); R = {"run_id": run_id, "out": str(out), "mcp_calls": [], "checks": {}}
    try:
        before = _bal(mcp.call("get_balances")); R["sandbox_before"] = before
        rates = live_rates_to_usd(c, ["EUR", "GBP"]); R["rates_to_usd"] = rates
        pol = Policy.load(ROOT / "policies/default.yaml")
        log.append("live_run", {"run_id": run_id, "sandbox_balances": before, "rates": rates, "source": "mcp:get_balances"})
        loop = PlanLoop(pol, OBLIGATIONS, rates, log, [make_proposer("naive"), make_proposer("planner")])
        res = loop.run(dict(SEED_BALANCES), confidence)
        R["loop"] = {"rounds": [(r.proposer, r.round, r.report.verified) for r in res.rounds],
                     "winner": res.winner and res.winner.proposer, "winner_cost_usd": res.winner and res.winner.cost,
                     "first_feedback": [f.as_dict() for f in res.rounds[0].report.feedback][:3]}
        bens = {o.id: BENEFICIARIES["DE" if o.currency == "EUR" else "US"] for o in OBLIGATIONS if o.currency in ("USD", "EUR")}
        ns = uuid.uuid5(uuid.NAMESPACE_URL, f"glassbox-live-{run_id}")
        ex = Executor(c, log, ApprovalBook(out / "approvals.json"), out / "ledger.json", policy=pol, ns=ns)
        R["execute"] = loop.execute_winner(res, ex, bens)
        n_ledger = len(ex.ledger)
        ex2 = Executor(c, log, ApprovalBook(out / "approvals.json"), out / "ledger.json", policy=pol, ns=ns)  # restart
        R["execute_repeat"] = loop.execute_winner(res, ex2, bens)
        R["checks"]["duplicate_rerun_no_new_ops"] = len(ex2.ledger) == n_ledger and all(
            v in ("ALREADY_DONE", "NOT_EXECUTABLE", "AWAITING_APPROVAL") for v in R["execute_repeat"].values())
        convs = [r["result"] for k, r in ex.ledger.items() if k.startswith("CONVERT:") and r.get("status") == "DONE"]
        R["conversions"] = [{k: x.get(k) for k in ("quote_id", "rate", "conversion_id", "status", "buy_amount", "sell_amount")} for x in convs]
        R["checks"]["conversion_cites_quote"] = all(x.get("quote_id") for x in convs)
        # Finding 2026-10-28: the sandbox ACCEPTED a second conversion on a used quote_id (it moved money). Single-use is
        # therefore enforced by our executor (quote persisted per op, never reused), not assumed from the API. Off by default.
        if convs and probe_quote_reuse:
            try:
                c.create_conversion(convs[0]["quote_id"], str(uuid.uuid4())); R["quote_reuse_live"] = "accepted"
            except AirwallexError as e:
                R["quote_reuse_live"] = f"rejected {e.status} {e.body.get('code')}"
        tids = sorted({(r["result"].get("id") or r["result"].get("transfer_id")) for k, r in ex.ledger.items()
                       if k.split(":")[0] in ("FUND", "PAY") and r.get("status") == "DONE"})
        trk, R["transfers"], R["simulation_errors"] = TransferTracker(c, log, sleep=sleep, interval=2), {}, []
        for tid in tids:
            R["simulation_errors"] += simulate_paid(mcp, c, tid, sleep)
            R["transfers"][tid] = trk.wait(tid, max_polls=4 if R["simulation_errors"] else 8)
        if R["simulation_errors"]:
            log.append("external_blocker", {"what": "sandbox transfer simulation", "errors": R["simulation_errors"]})
        R["checks"]["all_transfers_paid"] = bool(tids) and all(t["status"] == "PAID" for t in R["transfers"].values())
        ga = mcp.call("list_global_accounts"); ga = ga if isinstance(ga, list) else ga.get("items", [])
        from .mcp_client import MCPError
        src = "mcp:simulate_create_deposit"
        try:
            dep = mcp.call("simulate_create_deposit", {"globalAccountId": ga[0]["id"], "amount": float(deposit_usd)})
        except MCPError as e:   # MCP tool bug 2026-10-28: 400 "global account not associated" for a valid ACTIVE GA
            R["mcp_errors"] = R.get("mcp_errors", []) + [{"tool": "simulate_create_deposit", "error": str(e)[:200]}]
            try:
                dep, src = c.simulate_deposit("USD", deposit_usd, ga[0]["id"]), "rest:/simulation/deposit/create"
            except AirwallexError as e2:   # sandbox simulation incident (status page, unresolved since Oct 26)
                R["simulation_errors"].append({"transfer": None, "status": "DEPOSIT", "mcp": str(e)[:160],
                                               "rest": f"{e2.status} {e2.body.get('code')}"})
                dep, src = None, "unavailable"
        R["checks"]["deposit_simulated"] = src != "unavailable"
        log.append("deposit", {"source": src, "currency": "USD", "amount": deposit_usd,
                               "status": (dep or {}).get("status") if isinstance(dep, dict) else str(dep)[:80]})
        p = Planner(pol, OBLIGATIONS, rates, log); p.plan(dict(SEED_BALANCES), confidence)
        bal2 = dict(SEED_BALANCES); bal2["USD"] += deposit_usd
        R["recalc"] = p.on_deposit("USD", bal2)
        sleep(2)
        after = _bal(mcp.call("get_balances")); R["sandbox_after"] = after
        R["sandbox_delta"] = {k: after.get(k, D(0)) - before.get(k, D(0)) for k in ("USD", "EUR", "GBP")}
        R["checks"]["audit_chain_valid"] = log.verify()
        rp = replay(log.path, pol, list(OBLIGATIONS)); R["checks"]["replay_ok"] = rp["ok"]
        R["mcp_calls"] = list(mcp.calls)
        R["checks"]["mcp_used"] = {"get_balances", "list_global_accounts"} <= set(mcp.calls)
        # balance delta must equal executed outflows (transfer debits + conversion legs + deposit), within 1 unit per ccy
        exp = expected_delta(ex.ledger, deposit_usd if src != "unavailable" else D(0))
        R["expected_delta"] = exp
        R["checks"]["balances_reconcile"] = all(abs(R["sandbox_delta"][k] - exp[k]) <= 1 for k in exp)
        R["pass"] = all(R["checks"].values())
        return R
    finally:
        (out / "s20_result.json").write_text(json.dumps(R, indent=1, default=str))
        if own_mcp:
            mcp.close()
