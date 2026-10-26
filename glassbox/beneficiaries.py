"""Beneficiary payload builder for the three demo corridors, encoding the builder-guide rules:
US/USD LOCAL: flat bank_details, account_routing_type1 "aba", bank_account_category exactly "Checking"/"Savings".
GB/GBP LOCAL: bank_name required, routing type lowercase "sort_code".
DE/EUR SWIFT: IBAN + swift_code, no routing fields.
Wrapper is {"beneficiary": {...}, "transfer_methods": [...]} (not payment_methods). Validate live with
POST /beneficiaries/validate before create (verified OK in sandbox 2026-10-26)."""
from __future__ import annotations


class BeneficiaryError(ValueError):
    pass


def iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if len(s) < 15 or not s[:2].isalpha():
        return False
    n = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(n) % 97 == 1


def build_beneficiary(country: str, *, name: str, address: dict | None = None, account_number: str | None = None,
                      routing: str | None = None, iban: str | None = None, swift_code: str | None = None,
                      bank_name: str | None = None, category: str = "Checking", nickname: str | None = None) -> dict:
    address = dict(address or {})
    if country == "US":
        if category not in ("Checking", "Savings"):
            raise BeneficiaryError("bank_account_category must be exactly Checking or Savings (code 016)")
        need = ["street_address", "city", "state", "postcode"]
        bd = {"account_currency": "USD", "account_routing_type1": "aba", "account_routing_value1": routing,
              "account_number": account_number, "bank_account_category": category}
        methods = ["LOCAL"]
    elif country == "GB":
        if not bank_name:
            raise BeneficiaryError("GB requires bank_name")
        need = ["street_address", "city"]
        bd = {"account_currency": "GBP", "account_routing_type1": "sort_code", "account_routing_value1": routing,
              "account_number": account_number, "bank_name": bank_name}
        methods = ["LOCAL"]
    elif country == "DE":
        if not (iban and iban_ok(iban)):
            raise BeneficiaryError("DE requires a valid IBAN (checksum)")
        if not (swift_code and bank_name):
            raise BeneficiaryError("DE SWIFT requires swift_code and bank_name")
        need = ["street_address", "city"]
        bd = {"account_currency": "EUR", "iban": iban.replace(" ", ""), "swift_code": swift_code, "bank_name": bank_name}
        methods = ["SWIFT"]
    else:
        raise BeneficiaryError(f"corridor {country} not supported; check /beneficiaries/schema first")
    missing = [f for f in need if not address.get(f)] + [k for k, v in bd.items() if v is None]
    if missing:
        raise BeneficiaryError(f"missing fields: {missing}")
    address["country_code"] = country
    bd.update(bank_country_code=country, account_name=name)
    return {"beneficiary": {"entity_type": "COMPANY", "company_name": name, "address": address, "bank_details": bd},
            "transfer_methods": methods, "nickname": nickname or name}
