from __future__ import annotations

import pandas as pd

from app.adapters.base import BaseSourceAdapter
from app.core.normalizer import (
    clean_text,
    normalize_reference,
    normalize_status,
    normalize_user,
    parse_amount,
    parse_datetime,
)
from app.models.transaction import SourceTransaction


class VATransactionAdapter(BaseSourceAdapter):
    source_type = "va_transaction"
    required_columns = ("ID", "Date Time", "User", "Txn ID", "UTR", "VA No", "Amount", "Status")

    def normalize(self, frame: pd.DataFrame) -> list[SourceTransaction]:
        transactions: list[SourceTransaction] = []
        for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
            raw = self.row_dict(row)
            utr = normalize_reference(row.get("UTR"))
            transactions.append(
                SourceTransaction(
                    source_type=self.source_type,
                    source_transaction_id=clean_text(row.get("Txn ID")) or clean_text(row.get("ID")),
                    utr=utr,
                    reference_number=utr,
                    amount=parse_amount(row.get("Amount")),
                    user=normalize_user(row.get("User")),
                    transaction_date=parse_datetime(row.get("Date Time")),
                    status=normalize_status(row.get("Status")),
                    virtual_account_number=clean_text(row.get("VA No")),
                    source_row_number=row_number,
                    raw_data=raw,
                )
            )
        return transactions
