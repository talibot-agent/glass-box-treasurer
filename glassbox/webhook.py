"""Webhook receiver. Airwallex signs each delivery: x-signature = hex HMAC-SHA256(secret, x-timestamp + raw body).
Verify on the RAW bytes (never re-serialised JSON), reject stale timestamps, dedupe on event id (at-least-once,
persisted), ack 200 fast, and never rely on delivery order (use created_at)."""
from __future__ import annotations
import hashlib, hmac, json, time
from pathlib import Path

TOLERANCE_S = 300


class WebhookReceiver:
    def __init__(self, secret: str, seen_path, audit, on_event, clock=time.time):
        self._secret = secret.encode()
        self.path, self.audit, self.on_event, self.clock = Path(seen_path), audit, on_event, clock
        self._seen = set(json.loads(self.path.read_text())) if self.path.exists() else set()

    def __repr__(self):
        return "WebhookReceiver(secret=***)"

    def handle(self, headers: dict, raw: bytes) -> int:
        h = {k.lower(): v for k, v in headers.items()}
        ts, sig = h.get("x-timestamp"), h.get("x-signature")
        if not ts or not sig:
            return 401
        try:
            age = abs(self.clock() - int(ts) / 1000)
        except ValueError:
            return 401
        want = hmac.new(self._secret, ts.encode() + raw, hashlib.sha256).hexdigest()
        if age > TOLERANCE_S or not hmac.compare_digest(want, sig):
            self.audit.append("webhook_rejected", {"reason": "signature/timestamp", "ts": ts})
            return 401
        evt = json.loads(raw)
        eid = evt.get("id")
        if eid in self._seen:
            return 200
        self._seen.add(eid); self.path.write_text(json.dumps(sorted(self._seen)))
        self.audit.append("webhook", {"id": eid, "name": evt.get("name"), "created_at": evt.get("created_at")})
        self.on_event(evt)
        return 200


def serve(receiver: WebhookReceiver, port: int = 8787):  # pragma: no cover - thin stdlib wrapper
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            code = receiver.handle(dict(self.headers), raw)
            self.send_response(code); self.end_headers()

        def log_message(self, *a):
            pass
    HTTPServer(("0.0.0.0", port), H).serve_forever()
