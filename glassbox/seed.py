"""Kit 1 scenario: five obligations across USD/EUR/GBP due within 72h; cash covers only part after floors."""
from decimal import Decimal as D
from .models import Obligation

SEED_BALANCES = {"USD": D("12000"), "EUR": D("1500"), "GBP": D("600")}

OBLIGATIONS = [
    Obligation("OB-SHIP", "Maersk Freight", "shipping", "USD", D("6500"), 24),
    Obligation("OB-SUPP", "Lyon Textiles SARL", "supplier", "EUR", D("4200"), 36, method="SWIFT"),
    Obligation("OB-SAAS", "Figma", "saas", "USD", D("450"), 48),
    Obligation("OB-NEWV", "Brightline Packaging Ltd", "supplier", "GBP", D("9800"), 60, beneficiary_verified=False),
    Obligation("OB-PAY", "Gusto Payroll", "payroll", "USD", D("3200"), 72),
]
