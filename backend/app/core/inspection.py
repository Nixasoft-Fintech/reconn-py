from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

import pandas as pd

from app.models.transaction import SourceTransaction


def inspect_report(
    frame: pd.DataFrame, transactions: list[SourceTransaction]
) -> dict[str, Any]:
    statuses = Counter(tx.status or "missing" for tx in transactions)
    amounts = [tx.amount for tx in transactions if tx.amount is not None]
    total_amount = sum(amounts, Decimal("0.00"))

    reference_counts = Counter(
        tx.reference_number for tx in transactions if tx.reference_number
    )
    duplicate_references = sorted(
        ref for ref, count in reference_counts.items() if count > 1
    )

    missing_by_column = {
        str(column): int(
            frame[column]
            .map(lambda value: value is None or str(value).strip() == "")
            .sum()
        )
        for column in frame.columns
    }

    amount_by_status: dict[str, dict[str, Any]] = {}
    for status in sorted(statuses):
        status_transactions = [
            tx for tx in transactions if (tx.status or "missing") == status
        ]
        status_amounts = [
            tx.amount for tx in status_transactions if tx.amount is not None
        ]
        amount_by_status[status] = {
            "count": len(status_transactions),
            "amount": str(sum(status_amounts, Decimal("0.00"))),
        }

    return {
        "record_count": len(transactions),
        "columns": [str(column) for column in frame.columns],
        "status_counts": dict(statuses),
        "missing_values_by_column": missing_by_column,
        "duplicate_reference_count": len(duplicate_references),
        "duplicate_references": duplicate_references,
        "amount_summary": {
            "valid_amount_count": len(amounts),
            "invalid_or_missing_amount_count": len(transactions) - len(amounts),
            "total_amount": str(total_amount),
            "amount_by_status": amount_by_status,
        },
        "invalid_date_count": sum(tx.transaction_date is None for tx in transactions),
        "missing_user_count": sum(tx.user is None for tx in transactions),
    }
