import pandas as pd

from app.adapters.bank_statement_adapter import (
    BankColumnMapping,
    BankStatementAdapter,
)


def test_extracts_utr_from_imps_narration():
    frame = pd.DataFrame(
        [
            {
                "Reference": "BANKREF123",
                "Amount": "87800",
                "Date": "11-11-2026",
                "Narration": "IMPS/P2A/631418480656/HDFC/",
            }
        ]
    )

    mapping = BankColumnMapping(
        reference_number="Reference",
        amount="Amount",
        transaction_date="Date",
        narration="Narration",
    )

    transactions = BankStatementAdapter(mapping).normalize(frame)

    transaction = transactions[0]

    assert transaction.reference_number == "BANKREF123"
    assert "631418480656" in transaction.extracted_references
    assert "631418480656" in transaction.reference_keys()
    assert transaction.amount == 87800


def test_preserves_original_reference_and_extracts_neft_utr():
    frame = pd.DataFrame(
        [
            {
                "Reference": "BANKREF456",
                "Amount": "20000",
                "Date": "11-11-2026",
                "Narration": "NEFT/AXISCN123456789012/NEXASOFT",
            }
        ]
    )

    mapping = BankColumnMapping(
        reference_number="Reference",
        amount="Amount",
        transaction_date="Date",
        narration="Narration",
    )

    transaction = BankStatementAdapter(mapping).normalize(frame)[0]

    assert transaction.reference_number == "BANKREF456"
    assert "AXISCN123456789012" in transaction.extracted_references
    assert "BANKREF456" in transaction.reference_keys()


def test_no_extracted_reference_for_normal_narration():
    frame = pd.DataFrame(
        [
            {
                "Reference": "BANKREF789",
                "Amount": "1000",
                "Date": "11-11-2026",
                "Narration": "Monthly account maintenance charges",
            }
        ]
    )

    mapping = BankColumnMapping(
        reference_number="Reference",
        amount="Amount",
        transaction_date="Date",
        narration="Narration",
    )

    transaction = BankStatementAdapter(mapping).normalize(frame)[0]

    assert transaction.extracted_references == []
    assert transaction.reference_keys() == {"BANKREF789"}
