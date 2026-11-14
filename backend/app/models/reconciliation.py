from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.models.bank_transaction import BankTransaction
from app.models.transaction import SourceTransaction


@dataclass(slots=True)
class MatchResult:
    source: SourceTransaction
    bank: BankTransaction | None
    final_status: str
    reference_status: str
    amount_status: str
    user_status: str
    date_status: str
    exception_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source.source_type,
            "source_transaction_id": self.source.source_transaction_id,
            "source_row_number": self.source.source_row_number,
            "source_reference": self.source.utr or self.source.reference_number,
            "source_amount": (
                str(self.source.amount) if self.source.amount is not None else None
            ),
            "source_user": self.source.user,
            "source_date": (
                self.source.transaction_date.isoformat()
                if self.source.transaction_date
                else None
            ),
            "bank_row_number": self.bank.bank_row_number if self.bank else None,
            "bank_utr": self.bank.utr if self.bank else None,
            "bank_reference": self.bank.reference_number if self.bank else None,
            "bank_amount": (
                str(self.bank.amount)
                if self.bank and self.bank.amount is not None
                else None
            ),
            "bank_user": self.bank.user if self.bank else None,
            "bank_date": (
                self.bank.transaction_date.isoformat()
                if self.bank and self.bank.transaction_date
                else None
            ),
            "reference_status": self.reference_status,
            "amount_status": self.amount_status,
            "user_status": self.user_status,
            "date_status": self.date_status,
            "final_status": self.final_status,
            "exception_reason": self.exception_reason,
        }


@dataclass(slots=True)
class ReconciliationOutput:
    results: list[MatchResult]
    bank_transactions: list[BankTransaction]
    unmatched_bank_rows: list[BankTransaction]
    summary: dict[str, Any]

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "results": [item.to_dict() for item in self.results],
            "unmatched_bank_transactions": [
                item.to_dict(include_raw_data=include_raw)
                for item in self.unmatched_bank_rows
            ],
        }
