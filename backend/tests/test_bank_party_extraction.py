from decimal import Decimal

import pandas as pd

from app.adapters.bank_statement_adapter import (
    BankColumnMapping,
    BankStatementAdapter,
)


def test_extract_party_from_narration_when_user_column_missing():
    frame = pd.DataFrame(
        [
            {
                "Reference": "123456789",
                "Amount": "998909.00",
                "Narration": ("HDFCR52026111084124582 " "BRACE VENTURES PRIVATE LI"),
                "Type": "CR",
            }
        ]
    )

    mapping = BankColumnMapping(
        reference_number="Reference",
        amount="Amount",
        narration="Narration",
        transaction_type="Type",
    )

    adapter = BankStatementAdapter(mapping)

    transactions = adapter.normalize(frame)

    assert len(transactions) == 1
    assert transactions[0].user == "BRACE VENTURES PRIVATE LI"
    assert transactions[0].amount == Decimal("998909.00")
