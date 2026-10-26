import pytest
from glassbox.beneficiaries import build_beneficiary, BeneficiaryError

def test_us_local_payload_follows_guide():
    b = build_beneficiary("US", name="Maersk Demo", account_number="123456789", routing="021000021",
                          address={"street_address": "1 St", "city": "NY", "state": "NY", "postcode": "10001"})
    bd = b["beneficiary"]["bank_details"]
    assert b["transfer_methods"] == ["LOCAL"] and "payment_methods" not in b
    assert bd["account_routing_type1"] == "aba" and bd["bank_account_category"] == "Checking"
    assert bd["account_currency"] == "USD" and b["beneficiary"]["address"]["country_code"] == "US"

def test_gb_requires_bank_name_and_lowercase_sort_code():
    with pytest.raises(BeneficiaryError):
        build_beneficiary("GB", name="X", account_number="12345678", routing="040004", address={"street_address": "a", "city": "L"})
    b = build_beneficiary("GB", name="X", account_number="12345678", routing="040004", bank_name="Demo Bank",
                          address={"street_address": "a", "city": "London", "postcode": "E1 6AN"})
    assert b["beneficiary"]["bank_details"]["account_routing_type1"] == "sort_code"

def test_de_swift_uses_iban_and_no_routing():
    b = build_beneficiary("DE", name="Lyon", iban="DE89370400440532013000", swift_code="COBADEFFXXX", bank_name="Commerzbank",
                          address={"street_address": "a", "city": "Berlin", "postcode": "10115"})
    bd = b["beneficiary"]["bank_details"]
    assert b["transfer_methods"] == ["SWIFT"] and "account_routing_type1" not in bd and bd["iban"].startswith("DE")

def test_bad_iban_checksum_rejected_locally():
    with pytest.raises(BeneficiaryError):
        build_beneficiary("DE", name="L", iban="DE00370400440532013000", swift_code="COBADEFFXXX", bank_name="B",
                          address={"street_address": "a", "city": "Berlin"})

def test_unknown_corridor():
    with pytest.raises(BeneficiaryError):
        build_beneficiary("FR", name="x")
