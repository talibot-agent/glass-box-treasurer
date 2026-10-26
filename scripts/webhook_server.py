"""Run the webhook receiver; events update out/transfer_state.json. Secret from AWX_WEBHOOK_SECRET or
/workspace/projects/_secrets/airwallex_webhook.txt (never printed). Raw deliveries (headers only, no secret) are logged."""
import os, sys, pathlib, json
root = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root))
from glassbox.audit import AuditLog
from glassbox.tracking import TransferState
from glassbox.webhook import WebhookReceiver, serve
out = root / "out"; out.mkdir(exist_ok=True)
def secret():
    if os.environ.get("AWX_WEBHOOK_SECRET"): return os.environ["AWX_WEBHOOK_SECRET"]
    p = pathlib.Path("/workspace/projects/_secrets/airwallex_webhook.txt")
    return p.read_text().strip().split("=", 1)[-1] if p.exists() else ""
audit = AuditLog(out / "webhook_audit.jsonl"); state = TransferState(out / "transfer_state.json", audit)
def on_event(evt):
    with open(out / "webhook_events.jsonl", "a") as f: f.write(json.dumps({"id": evt.get("id"), "name": evt.get("name")}) + "\n")
    state.apply_event(evt)
serve(WebhookReceiver(secret(), out / "wh_seen.json", audit, on_event=on_event), int(sys.argv[1]) if len(sys.argv) > 1 else 8787)
