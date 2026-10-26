"""Smoke: login + balances + global accounts against the Airwallex sandbox. Prints no secrets.
Usage: python3 scripts/smoke.py [--fund USD 25000]"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from glassbox.http_client import AirwallexHTTP

c = AirwallexHTTP.from_secrets()
c.login(); print("auth: ok", c.base)
print("balances (non-zero):", {k: str(v) for k, v in c.balances().items() if v} or "(none)")
try:
    gas = c.global_accounts()
except Exception as e:
    gas = []; print("global accounts: unavailable", type(e).__name__)
print("global accounts:", [(g.get("currency"), g.get("status")) for g in gas] if gas else "(none)")
if "--fund" in sys.argv:
    i = sys.argv.index("--fund"); ccy, amt = sys.argv[i + 1], sys.argv[i + 2]
    ga = next((g["id"] for g in gas if g.get("currency") == ccy), None)
    print("deposit:", c.simulate_deposit(ccy, amt, ga).get("status"))
    print("balances:", {k: str(v) for k, v in c.balances().items()})
