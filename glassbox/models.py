from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Action(str, Enum):
    FUND = "FUND"
    CONVERT = "CONVERT"
    DEFER = "DEFER"
    ESCALATE = "ESCALATE"


class Verdict(str, Enum):
    APPROVE = "APPROVE"            # may execute autonomously
    NEEDS_HUMAN = "NEEDS_HUMAN"    # execute only after bound human approval
    REJECT = "REJECT"              # never execute


@dataclass(frozen=True)
class Obligation:
    id: str
    counterparty: str
    category: str
    currency: str
    amount: Decimal
    due_hours: int
    method: str = "LOCAL"          # LOCAL or SWIFT
    beneficiary_verified: bool = True


@dataclass(frozen=True)
class Proposal:
    obligation_id: str
    action: Action
    # for CONVERT: sell/buy legs in major units
    sell_currency: str | None = None
    buy_currency: str | None = None
    buy_amount: Decimal | None = None
    evidence: tuple[str, ...] = ()


@dataclass
class Decision:
    proposal: Proposal
    verdict: Verdict
    clauses: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
