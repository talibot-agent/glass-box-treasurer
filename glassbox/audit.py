"""Append-only, hash-chained audit log (JSONL). Replayable and tamper-evident."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

GENESIS = "0" * 64


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.touch(exist_ok=True)

    def entries(self) -> list[dict]:
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def head(self) -> str:
        e = self.entries()
        return e[-1]["hash"] if e else GENESIS

    def append(self, kind: str, payload: dict) -> dict:
        prev = self.head()
        seq = len(self.entries())
        body = {"seq": seq, "kind": kind, "payload": payload, "prev": prev}
        body["hash"] = hashlib.sha256(_canon(body).encode()).hexdigest()
        with self.path.open("a") as f:
            f.write(_canon(body) + "\n")
        return body

    def verify(self) -> bool:
        prev = GENESIS
        for i, e in enumerate(self.entries()):
            h = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev"] != prev or e["seq"] != i:
                return False
            if hashlib.sha256(_canon(body).encode()).hexdigest() != h:
                return False
            prev = h
        return True
