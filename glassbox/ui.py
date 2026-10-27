"""Read-mostly web dashboard: plan, decisions with cited policy clauses, approvals (approve button), transfer
outcomes and the hash-chained audit log. stdlib only; binds 127.0.0.1. Approving here only marks a binding APPROVED;
execution still goes through Executor, which re-checks the exact binding digest."""
import html, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from .approvals import ApprovalMismatch

e = lambda x: html.escape(str(x))
CSS = """body{font:14px system-ui;margin:2em;max-width:1200px}table{border-collapse:collapse;width:100%;margin-bottom:1.5em}
td,th{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}th{background:#f4f4f4}
.APPROVE{color:#070}.REJECT{color:#a00}.NEEDS_HUMAN{color:#b60}code{font-size:12px}.note{color:#666}"""


class Dashboard:
    def __init__(self, planner, book, audit, transfers=None, loop=None):
        self.planner, self.book, self.audit, self.transfers = planner, book, audit, transfers
        self.loop = loop  # optional verify_loop.LoopResult: shows rejected proposals + verifier feedback + winner

    def state(self):
        plan = [{"obligation": k, "action": v.action.value, "verdict": v.verdict.value, "clauses": v.clauses,
                 "reasons": v.reasons, "sell": v.proposal.sell_currency, "buy": v.proposal.buy_currency,
                 "amount": str(v.proposal.buy_amount)} for k, v in sorted(self.planner.current.items())]
        return {"plan": plan, "approvals": self.book.all(), "transfers": self.transfers.all() if self.transfers else {},
                "audit": self.audit.entries(), "audit_valid": self.audit.verify(),
                "loop": self.loop.ui_view() if self.loop else None}

    def render(self):
        s = self.state(); o = [f"<!doctype html><meta charset=utf-8><title>Glass Box Treasurer</title><style>{CSS}</style>",
             "<h1>Glass Box Treasurer</h1><p class=note>Built with AI assistance (Grok Bot agent); every decision below cites "
             "the deterministic policy clause that produced it. Sandbox only.</p>",
             "<h2>Plan &amp; decisions</h2><table><tr><th>Obligation<th>Action<th>Amount<th>Verdict<th>Cited rules<th>Reasons"]
        for p in s["plan"]:
            o.append(f"<tr><td>{e(p['obligation'])}<td>{e(p['action'])} {e(p['sell'] or '')}→{e(p['buy'] or '')}<td>{e(p['amount'])}"
                     f"<td class={e(p['verdict'])}>{e(p['verdict'])}<td>{e(', '.join(p['clauses']) or 'all clauses pass')}<td>{e('; '.join(p['reasons']))}")
        if s["loop"]:
            L = s["loop"]; w = L["winner"]
            o.append("</table><h2>Verify &amp; revise loop</h2><p class=note>Proposals are checked by the deterministic verifier "
                     "(validity → policy → dry run). Rejected rounds and the feedback that fixed them are kept. Winner: "
                     f"<b>{e(w['proposer'] if w else 'none, so nothing executes')}</b>"
                     f"{e(' at verified cost ' + w['cost_usd'] + ' USD') if w else ''}</p>"
                     "<table><tr><th>Proposer<th>Round<th>Verified<th>Cost USD<th>Plan<th>Feedback")
            for r in L["rounds"]:
                plan = "; ".join(f"{p['obligation']} {p['action']}" for p in r["plan"])
                fb = "; ".join(f"[{f['code']} {f['clause'] or ''}] {f['message']}" for f in r["feedback"]) or "none"
                o.append(f"<tr><td>{e(r['proposer'])}<td>{e(r['round'])}<td class={'APPROVE' if r['verified'] else 'REJECT'}>"
                         f"{e(r['verified'])}<td>{e(r['cost_usd'])}<td>{e(plan)}<td>{e(fb)}")
        o.append("</table><h2>Approvals</h2><table><tr><th>Token<th>Binding (exact)<th>Status<th>")
        for tok, a in s["approvals"].items():
            b = a["binding"]; btn = (f"<form method=post action=/approve><input type=hidden name=token value={e(tok)}>"
                   "<input name=approver placeholder=name required><button>Approve</button></form>") if a["status"] == "PENDING" else e(a.get("approver", ""))
            o.append(f"<tr><td><code>{e(tok[:12])}…</code><td>{e(b.get('action'))} {e(b.get('amount'))} {e(b.get('currency'))} → "
                     f"{e(b.get('counterparty'))} ({e(b.get('obligation_id'))})<td>{e(a['status'])}<td>{btn}")
        o.append("</table><h2>Transfers</h2><table><tr><th>Id<th>Status<th>Outcome<th>Failure<th>Source")
        for t in s["transfers"].values():
            o.append(f"<tr><td><code>{e(t['id'])}</code><td>{e(t['status'])}<td>{e(t['outcome'])}<td>{e(t.get('failure_type') or '')} "
                     f"{e(t.get('failure_reason') or '')}<td>{e(t.get('source'))}")
        o.append(f"</table><h2>Audit log ({len(s['audit'])} entries, chain valid: {s['audit_valid']})</h2>"
                 "<table><tr><th>#<th>Kind<th>Payload<th>Hash")
        for i, en in enumerate(s["audit"][-200:]):
            o.append(f"<tr><td>{i}<td>{e(en.get('kind'))}<td><code>{e(json.dumps(en.get('payload'), default=str)[:300])}</code>"
                     f"<td><code>{e(str(en.get('hash', ''))[:10])}</code>")
        return "".join(o) + "</table>"


def make_server(dash, port=8800, host="127.0.0.1"):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path == "/api/state": return self._send(200, json.dumps(dash.state(), default=str), "application/json")
            if self.path == "/": return self._send(200, dash.render())
            self._send(404, "not found")
        def do_POST(self):
            if self.path != "/approve": return self._send(404, "not found")
            q = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            tok, who = q.get("token", [""])[0], q.get("approver", [""])[0].strip()
            if not who: return self._send(400, "approver required")
            try: dash.book.approve(tok, who)
            except ApprovalMismatch as ex: return self._send(409, e(ex))
            dash.audit.append("approval", {"token": tok, "approver": who})
            self._send(200, "approved. <a href=/>back</a>")
    return ThreadingHTTPServer((host, port), H)
