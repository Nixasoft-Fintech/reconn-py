import pandas as pd
import pytest

from app.core.file_reader import BankStatementReadResult
from app.adapters.bank_statement_adapter import (
    normalize_bank_statement,
)


def make_read_result(frame, filename, metadata=None):
    return BankStatementReadResult(
        frame=frame,
        source_file=filename,
        header_row_number=1,
        metadata=metadata or {},
    )


def test_automatic_axis_normalization():
    frame = pd.DataFrame(
        [
            {
                "Transaction Date (dd/mm/yyyy)": "11/11/2026",
                "Value Date (dd/mm/yyyy)": "11/11/2026",
                "Particulars": "IMPS/P2A/631418480656/HDFC/",
                "Amount(INR)": "87800",
                "Debit/Credit": "DR",
                "Cheque Number": "",
                "Balance(INR)": "100000",
            }
        ]
    )

    result = make_read_result(
        frame,
        "axis_statement.csv",
        {"account_number": "926020004794821"},
    )

    profile, transactions = normalize_bank_statement(result)

    assert profile.bank_format == "axis"
    assert len(transactions) == 1

    transaction = transactions[0]

    assert transaction.bank_name == "Axis Bank"
    assert transaction.bank_account_number == "926020004794821"
    assert transaction.direction == "DR"
    assert transaction.amount == 87800
    assert "631418480656" in transaction.extracted_references


def test_automatic_indusind_normalization():
    frame = pd.DataFrame(
        [
            {
                "Bank Reference": "BANKREF123",
                "Value Date": "11-11-2026",
                "Transaction Date & Time": "11-11-2026 10:30:00",
                "Type": "Debit",
                "Payment Narration": "IMPS/P2A/631418480656/HDFC/",
                "Debit": "87800",
                "Credit": "",
                "Available Balance": "100000",
            }
        ]
    )

    result = make_read_result(
        frame,
        "custom_statement.xlsx",
        {
            "account_number": "259082622344",
            "account_holder": "NEXASOFT TECHNOLOGIES PVT LTD",
        },
    )

    profile, transactions = normalize_bank_statement(result)

    transaction = transactions[0]

    assert profile.bank_format == "indusind"
    assert transaction.bank_name == "IndusInd Bank"
    assert transaction.account_holder == "NEXASOFT TECHNOLOGIES PVT LTD"
    assert transaction.reference_number == "BANKREF123"
    assert transaction.direction == "Debit"
    assert transaction.amount == 87800


def test_automatic_hdfc_normalization():
    frame = pd.DataFrame(
        [
            {
                "Date": "11/11/2026",
                "Narration": "IMPS/P2A/631418480656/HDFC/",
                "Chq./Ref.No.": "HDFCREF123",
                "Value Dt": "11/11/2026",
                "Withdrawal Amt.": "87800",
                "Deposit Amt.": "",
                "Closing Balance": "100000",
            }
        ]
    )

    result = make_read_result(
        frame,
        "hdfc_statement.xls",
        {"account_number": "50200108946770"},
    )

    profile, transactions = normalize_bank_statement(result)

    transaction = transactions[0]

    assert profile.bank_format == "hdfc"
    assert transaction.bank_name == "HDFC Bank"
    assert transaction.bank_account_number == "50200108946770"
    assert transaction.reference_number == "HDFCREF123"
    assert transaction.amount == 87800
    assert transaction.direction == "DEBIT"


def test_automatic_kotak_normalization():
    frame = pd.DataFrame(
        [
            {
                "Sl. No.": "1",
                "Transaction Date": "10-11-2026 12:11:18",
                "Value Date": "10-11-2026",
                "Description": "HDFCR52026111084124582 BRACE VENTURES",
                "Chq /Ref No.": "RTGSINW-0116145856",
                "Amount": "998909",
                "Dr / Cr": "CR",
                "Balance": "1000000",
                "Dr / Cr__2": "CR",
            }
        ]
    )

    result = make_read_result(
        frame,
        "kotak_statement.csv",
        {"account_number": "3550826229"},
    )

    profile, transactions = normalize_bank_statement(result)

    transaction = transactions[0]

    assert profile.bank_format == "kotak"
    assert transaction.bank_name == "Kotak Mahindra Bank"
    assert transaction.bank_account_number == "3550826229"
    assert transaction.reference_number == "RTGSINW-0116145856"
    assert transaction.direction == "CR"
    assert transaction.amount == 998909


def test_unsupported_bank_format_is_rejected():
    frame = pd.DataFrame(
        [
            {
                "Date": "11-11-2026",
                "Amount": "1000",
                "Details": "Test transaction",
            }
        ]
    )

    result = make_read_result(frame, "unknown.csv")

    with pytest.raises(ValueError, match="Unsupported bank statement format"):
        normalize_bank_statement(result)


def test_transaction_preserves_source_filename():
    frame = pd.DataFrame(
        [
            {
                "Transaction Date (dd/mm/yyyy)": "11/11/2026",
                "Value Date (dd/mm/yyyy)": "11/11/2026",
                "Particulars": "UPI PAYMENT",
                "Amount(INR)": "500",
                "Debit/Credit": "DR",
                "Cheque Number": "REF12345678",
                "Balance(INR)": "10000",
            }
        ]
    )

    result = make_read_result(frame, "axis_account_1.csv")

    _, transactions = normalize_bank_statement(result)

    assert transactions[0].source_filename == "axis_account_1.csv"
