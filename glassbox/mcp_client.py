"""Minimal stdio client for the official Airwallex Developer MCP (local package @airwallex/developer-mcp).
Used for the steps the MCP exposes (balances, FX quote, transfers, simulations); REST covers the gaps
(FX conversion has no MCP tool, per the builder guide). Credentials come from the secrets file via env, never printed."""
from __future__ import annotations
import json, os, subprocess, threading

from .http_client import load_creds

PKG = os.environ.get("AWX_MCP_PKG", "@airwallex/developer-mcp@latest")


class MCPError(Exception):
    pass


class DeveloperMCP:
    def __init__(self, cmd=None, env=None, timeout=90):
        cid, key = load_creds()
        e = {**os.environ, "AIRWALLEX_SANDBOX_CLIENT_ID": cid, "AIRWALLEX_SANDBOX_API_KEY": key,
             "DISABLE_TELEMETRY": "true", **(env or {})}
        self.p = subprocess.Popen(cmd or ["npx", "-y", PKG], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=e, text=True, bufsize=1)
        self.timeout, self._id, self.calls = timeout, 0, []
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "glass-box-treasurer", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def __repr__(self):
        return "DeveloperMCP(local)"

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()

    def _read(self):
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("l", self.p.stdout.readline()), daemon=True)
        t.start(); t.join(self.timeout)
        if "l" not in box or not box["l"]:
            raise MCPError("MCP server timed out or exited")
        return json.loads(box["l"])

    def _rpc(self, method, params):
        self._id += 1; rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            m = self._read()
            if m.get("id") == rid:
                if "error" in m:
                    raise MCPError(str(m["error"])[:300])
                return m["result"]

    def tools(self) -> list[str]:
        return [t["name"] for t in self._rpc("tools/list", {})["tools"]]

    def call(self, name, args=None):
        r = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        self.calls.append(name)
        text = "".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        if r.get("isError"):
            raise MCPError(f"{name}: {text[:300]}")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def close(self):
        try:
            self.p.stdin.close(); self.p.terminate(); self.p.wait(5)
        except Exception:
            self.p.kill()
