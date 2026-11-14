from __future__ import annotations

import re
import pandas as pd

from app.adapters.base import BaseSourceAdapter
from app.core.normalizer import (
    clean_text,
    normalize_status,
    normalize_user,
    parse_amount,
    parse_datetime,
    normalize_reference,
)
from app.models.transaction import SourceTransaction


class FundRequestAdapter(BaseSourceAdapter):
    source_type = "fund_request"

    required_columns = (
        "ID",
        "Date Time",
        "User",
        "Amount",
        "Ref No",
        "Status",
    )

    def normalize(self, frame: pd.DataFrame) -> list[SourceTransaction]:
        transactions: list[SourceTransaction] = []

        for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
            raw = self.row_dict(row)

            ref = clean_text(row.get("Ref No"))
            normalized_ref = normalize_reference(ref)

            # Recognize supported UTR formats conservatively.
            utr = None

            if normalized_ref:
                if re.fullmatch(r"\d{12}", normalized_ref):
                    utr = normalized_ref
                elif re.fullmatch(r"[A-Z]{4}[A-Z0-9]{14,18}", normalized_ref):
                    utr = normalized_ref

            transactions.append(
                SourceTransaction(
                    source_type=self.source_type,
                    source_transaction_id=clean_text(row.get("ID")),
                    utr=utr,
                    reference_number=normalized_ref,
                    amount=parse_amount(row.get("Amount")),
                    user=normalize_user(row.get("User")),
                    transaction_date=parse_datetime(row.get("Date Time")),
                    status=normalize_status(row.get("Status")),
                    bank=clean_text(row.get("Bank")),
                    payment_mode=clean_text(row.get("Mode")),
                    source_row_number=row_number,
                    raw_data=raw,
                )
            )

        return transactions
