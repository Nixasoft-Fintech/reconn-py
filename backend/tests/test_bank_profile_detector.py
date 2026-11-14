import pandas as pd
import pytest

from app.adapters.bank_profile_detector import (
    detect_bank_profile,
)


def test_detect_axis_statement():
    frame = pd.DataFrame(
        columns=[
            "S.NO",
            "Transaction Date (dd/mm/yyyy)",
            "Value Date (dd/mm/yyyy)",
            "Particulars",
            "Amount(INR)",
            "Debit/Credit",
            "Balance(INR)",
            "Cheque Number",
        ]
    )

    profile = detect_bank_profile(frame)

    assert profile.bank_name == "Axis Bank"
    assert profile.bank_format == "axis"
    assert profile.mapping["narration"] == "Particulars"


def test_detect_indusind_statement():
    frame = pd.DataFrame(
        columns=[
            "Bank Reference",
            "Value Date",
            "Transaction Date & Time",
            "Type",
            "Payment Narration",
            "Debit",
            "Credit",
            "Available Balance",
        ]
    )

    profile = detect_bank_profile(frame)

    assert profile.bank_format == "indusind"
    assert profile.mapping["debit_amount"] == "Debit"


def test_detect_hdfc_statement():
    frame = pd.DataFrame(
        columns=[
            "Date",
            "Narration",
            "Chq./Ref.No.",
            "Value Dt",
            "Withdrawal Amt.",
            "Deposit Amt.",
            "Closing Balance",
        ]
    )

    profile = detect_bank_profile(frame)

    assert profile.bank_format == "hdfc"
    assert profile.mapping["reference_number"] == "Chq./Ref.No."


def test_detect_kotak_statement():
    frame = pd.DataFrame(
        columns=[
            "Sl. No.",
            "Transaction Date",
            "Value Date",
            "Description",
            "Chq /Ref No.",
            "Amount",
            "Dr / Cr",
            "Balance",
            "Dr / Cr.1",
        ]
    )

    profile = detect_bank_profile(frame)

    assert profile.bank_format == "kotak"
    assert profile.mapping["transaction_type"] == "Dr / Cr"


def test_reject_unknown_statement():
    frame = pd.DataFrame(
        columns=[
            "Date",
            "Amount",
            "Details",
        ]
    )

    with pytest.raises(ValueError):
        detect_bank_profile(frame)
