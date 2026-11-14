import re
from dataclasses import dataclass

import pandas as pd


def normalize_column_name(value: object) -> str:
    """Normalize column names for reliable format detection."""
    value = str(value).strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("\ufeff", "")
    return value


@dataclass(slots=True)
class BankProfile:
    bank_name: str
    bank_format: str
    mapping: dict[str, str]


BANK_PROFILES = {
    "axis": {
        "required": {
            "transaction date (dd/mm/yyyy)",
            "particulars",
            "amount(inr)",
            "debit/credit",
        },
        "mapping": {
            "transaction_date": "Transaction Date (dd/mm/yyyy)",
            "value_date": "Value Date (dd/mm/yyyy)",
            "narration": "Particulars",
            "amount": "Amount(INR)",
            "transaction_type": "Debit/Credit",
            "reference_number": "Cheque Number",
            "balance": "Balance(INR)",
        },
    },
    "indusind": {
        "required": {
            "bank reference",
            "value date",
            "transaction date & time",
            "payment narration",
            "debit",
            "credit",
        },
        "mapping": {
            "reference_number": "Bank Reference",
            "value_date": "Value Date",
            "transaction_date": "Transaction Date & Time",
            "narration": "Payment Narration",
            "debit_amount": "Debit",
            "credit_amount": "Credit",
            "balance": "Available Balance",
            "transaction_type": "Type",
        },
    },
    "cub": {
        "required": {
            "txn date",
            "narrations",
            "debit",
            "credit",
        },
        "mapping": {
            "transaction_date": "Txn Date",
            "value_date": "Value Date",
            "reference_number": "Merchant Ref",
            "narration": "Narrations",
            "debit_amount": "Debit",
            "credit_amount": "Credit",
            "balance": "Balance",
        },
    },
    "hdfc": {
        "required": {
            "date",
            "narration",
            "chq./ref.no.",
            "withdrawal amt.",
            "deposit amt.",
        },
        "mapping": {
            "transaction_date": "Date",
            "narration": "Narration",
            "reference_number": "Chq./Ref.No.",
            "value_date": "Value Dt",
            "debit_amount": "Withdrawal Amt.",
            "credit_amount": "Deposit Amt.",
            "balance": "Closing Balance",
        },
    },
    "kotak": {
        "required": {
            "transaction date",
            "description",
            "chq /ref no.",
            "amount",
            "dr / cr",
        },
        "mapping": {
            "transaction_date": "Transaction Date",
            "value_date": "Value Date",
            "narration": "Description",
            "reference_number": "Chq /Ref No.",
            "amount": "Amount",
            "transaction_type": "Dr / Cr",
            "balance": "Balance",
        },
    },
}


def detect_bank_profile(frame: pd.DataFrame) -> BankProfile:
    """
    Identify the bank statement format using its transaction columns.

    Raises ValueError if no supported profile is recognized.
    """

    normalized_columns = {
        normalize_column_name(column): str(column) for column in frame.columns
    }

    for profile_name, profile in BANK_PROFILES.items():
        required = profile["required"]

        if required.issubset(normalized_columns.keys()):
            # Use the actual dataframe column names.
            mapping = {}

            for field, expected_column in profile["mapping"].items():
                normalized_expected = normalize_column_name(expected_column)

                if normalized_expected in normalized_columns:
                    mapping[field] = normalized_columns[normalized_expected]

            return BankProfile(
                bank_name={
                    "axis": "Axis Bank",
                    "hdfc": "HDFC Bank",
                    "kotak": "Kotak Mahindra Bank",
                    "indusind": "IndusInd Bank",
                    "cub": "City Union Bank",
                }[profile_name],
                bank_format=profile_name,
                mapping=mapping,
            )

    raise ValueError(
        "Unsupported bank statement format. " f"Detected columns: {list(frame.columns)}"
    )
