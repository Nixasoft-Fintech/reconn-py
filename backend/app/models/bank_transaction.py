from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class BankTransaction:
    bank_row_number: int

    utr: str | None
    reference_number: str | None
    amount: Decimal | None

    user: str | None
    transaction_date: datetime | None

    narration: str | None = None
    direction: str | None = None
    signed_amount: Decimal | None = None

    # Extracted references
    extracted_references: list[str] = field(default_factory=list)

    # Additional bank statement information
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder: str | None = None
    bank_format: str | None = None
    source_filename: str | None = None

    value_date: datetime | None = None

    # Original bank statement row
    raw_data: dict[str, Any] = field(default_factory=dict)

    def reference_keys(self) -> set[str]:
        references = {
            self.utr,
            self.reference_number,
            *self.extracted_references,
        }

        return {
            str(value).strip().upper()
            for value in references
            if value and str(value).strip()
        }

    def to_dict(self, include_raw_data: bool = False) -> dict[str, Any]:
        result = {
            "bank_row_number": self.bank_row_number,
            "utr": self.utr,
            "reference_number": self.reference_number,
            "amount": str(self.amount) if self.amount is not None else None,
            "user": self.user,
            "transaction_date": (
                self.transaction_date.isoformat() if self.transaction_date else None
            ),
            "value_date": (self.value_date.isoformat() if self.value_date else None),
            "narration": self.narration,
            "direction": self.direction,
            "signed_amount": (
                str(self.signed_amount) if self.signed_amount is not None else None
            ),
            "bank_name": self.bank_name,
            "bank_account_number": self.bank_account_number,
            "account_holder": self.account_holder,
            "bank_format": self.bank_format,
            "source_filename": self.source_filename,
            "extracted_references": self.extracted_references,
        }

        if include_raw_data:
            result["raw_data"] = self.raw_data

        return result
