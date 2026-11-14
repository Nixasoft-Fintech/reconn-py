from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class SourceTransaction:
    """Canonical representation of a source-report transaction.

    Original source values are retained in ``raw_data``. Bank-specific values
    and transaction direction are intentionally not inferred at this stage.
    """

    source_type: str
    source_transaction_id: str | None
    utr: str | None
    reference_number: str | None
    amount: Decimal | None
    user: str | None
    transaction_date: datetime | None
    status: str | None
    bank: str | None = None
    payment_mode: str | None = None
    virtual_account_number: str | None = None
    source_row_number: int | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-friendly dictionary without losing precision."""
        result = asdict(self)
        result["amount"] = str(self.amount) if self.amount is not None else None
        result["transaction_date"] = (
            self.transaction_date.isoformat() if self.transaction_date else None
        )
        return result
