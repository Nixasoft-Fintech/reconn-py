from decimal import Decimal

import pandas as pd
import pytest

from app.adapters.bank_statement_adapter import (
    BankColumnMapping,
    BankMappingError,
    BankStatementAdapter,
)


def test_bank_adapter_uses_explicit_mapping_and_preserves_direction_sign():
    frame = pd.DataFrame([{
        "UTR No": "  utr-001 ",
        "Ref": "REF 001",
        "Txn Amount": "(1,250.50)",
        "Beneficiary": "Acme Pvt Ltd",
        "Txn Date": "10-11-2026 10:00:00",
        "Narration": "Sample transfer",
    }])
    mapping = BankColumnMapping(
        utr="UTR No",
        reference_number="Ref",
        amount="Txn Amount",
        beneficiary_name="Beneficiary",
        transaction_date="Txn Date",
        narration="Narration",
    )
    item = BankStatementAdapter(mapping).normalize(frame)[0]
    assert item.utr == "UTR-001"
    assert item.reference_number == "REF001"
    assert item.amount == Decimal("1250.50")
    assert item.signed_amount == Decimal("-1250.50")
    assert item.direction is None  # Sign is retained; debit/credit meaning is not assumed.
    assert item.user == "ACME PVT LTD"
    assert item.raw_data["Narration"] == "Sample transfer"


def test_bank_adapter_supports_separate_debit_and_credit_columns():
    frame = pd.DataFrame([{
        "UTR": "U1", "Debit": "0", "Credit": "750.00", "DR/CR": "C",
    }])
    mapping = BankColumnMapping(
        utr="UTR", debit_amount="Debit", credit_amount="Credit",
        transaction_type="DR/CR",
    )
    item = BankStatementAdapter(mapping).normalize(frame)[0]
    assert item.amount == Decimal("750.00")
    assert item.direction == "C"


def test_mapping_requires_reference_and_amount():
    frame = pd.DataFrame([{"Narration": "abc"}])
    with pytest.raises(BankMappingError, match="reference field"):
        BankStatementAdapter(BankColumnMapping(narration="Narration")).normalize(frame)


def test_mapping_rejects_columns_not_present():
    frame = pd.DataFrame([{"UTR": "U1", "Amount": "100"}])
    mapping = BankColumnMapping(utr="UTR", amount="Amount", user="Customer")
    with pytest.raises(BankMappingError, match="not present"):
        BankStatementAdapter(mapping).normalize(frame)


def test_both_debit_and_credit_values_in_one_row_are_rejected():
    frame = pd.DataFrame([{"UTR": "U1", "Debit": "100", "Credit": "100"}])
    mapping = BankColumnMapping(utr="UTR", debit_amount="Debit", credit_amount="Credit")
    with pytest.raises(BankMappingError, match="both debit and credit"):
        BankStatementAdapter(mapping).normalize(frame)


def test_separate_amount_columns_keep_blank_amount_as_missing():
    frame = pd.DataFrame([{"UTR": "U1", "Debit": "", "Credit": ""}])
    mapping = BankColumnMapping(utr="UTR", debit_amount="Debit", credit_amount="Credit")
    item = BankStatementAdapter(mapping).normalize(frame)[0]
    assert item.amount is None
    assert item.signed_amount is None
