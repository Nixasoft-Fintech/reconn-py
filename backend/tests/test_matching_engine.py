from datetime import datetime
from decimal import Decimal
from unittest import result

import pytest

from app.matching.engine import reconcile_transactions, names_match
from app.models.bank_transaction import BankTransaction
from app.models.transaction import SourceTransaction


def source(
    *,
    source_type="fund_request",
    txn_id="SRC-1",
    amount="500.00",
    user="ACME FINTECH",
    status="approved",
):
    return SourceTransaction(
        source_type=source_type,
        source_transaction_id=txn_id,
        utr=None,
        reference_number=None,
        amount=Decimal(amount) if amount is not None else None,
        user=user,
        transaction_date=datetime.fromisoformat("2026-11-10 10:00:00"),
        status=status,
    )


def bank(
    *,
    row=2,
    amount="500.00",
    user="ACME FINTECH",
    direction="CREDIT",
):
    parsed_amount = Decimal(amount) if amount is not None else None

    return BankTransaction(
        bank_row_number=row,
        utr=None,
        reference_number=None,
        amount=parsed_amount,
        user=user,
        transaction_date=datetime.fromisoformat("2026-11-10 10:05:00"),
        direction=direction,
        signed_amount=parsed_amount,
        raw_data={},
    )


# ---------------------------------------------------------
# Name matching
# ---------------------------------------------------------


@pytest.mark.parametrize(
    "source_name, bank_name",
    [
        ("PAYSWITCH", "PAYSWITCH"),
        ("PAYSWITCH", "PAYWSITCH"),
        ("PAYSWITCH", "AYSWITCH"),
        ("PAYSWITCH", "PAYSWITCH INDIA"),
        ("PAY SWITCH", "PAYSWITCH"),
        ("PAYSWITCH", "PAYSWITCH INDIA PVT LTD"),
        ("PAYSWITCH", "INDIA PAYSWITCH PVT LTD"),
        ("PAYSWITCH", "PAYSWITCH PRIVATE LIMITED"),
        ("PAYSWITCH INDIA", "INDIA PAYSWITCH PVT LTD"),
        ("ACME FINTECH", "FINTECH ACME"),
    ],
)
def test_flexible_party_name_matching(source_name, bank_name):
    assert names_match(source_name, bank_name)


@pytest.mark.parametrize(
    "source_name, bank_name",
    [
        ("PAYSWITCH", "DROPBOTIX"),
        ("ACME", "XYZ TECHNOLOGIES"),
        ("PAYSWITCH", ""),
        ("", "PAYSWITCH"),
        (None, "PAYSWITCH"),
        ("PAYSWITCH", None),
    ],
)
def test_unrelated_or_missing_names_do_not_match(source_name, bank_name):
    assert not names_match(source_name, bank_name)


# ---------------------------------------------------------
# Exact amount + party name matching
# ---------------------------------------------------------


def test_exact_amount_and_name_match():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [bank(amount="1000.00", user="PAY SWITCH")],
    )

    assert len(result.results) == 1
    assert result.results[0].final_status == "MATCHED"
    assert result.results[0].bank is not None
    assert result.results[0].amount_status == "MATCH"
    assert result.results[0].user_status == "MATCH"


def test_wrong_amount_does_not_match():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [bank(amount="900.00", user="PAYSWITCH")],
    )

    assert result.results[0].final_status == "NOT_FOUND"
    assert result.results[0].bank is None


def test_wrong_party_name_does_not_match():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [bank(amount="1000.00", user="DROPBOTIX")],
    )

    assert result.results[0].final_status == "NOT_FOUND"
    assert result.results[0].bank is None


def test_both_amount_and_name_must_match():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [bank(amount="900.00", user="DROPBOTIX")],
    )

    assert result.results[0].final_status == "NOT_FOUND"


# ---------------------------------------------------------
# Bank credit / debit handling
# ---------------------------------------------------------


def test_bank_debit_is_not_eligible_for_matching():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [
            bank(
                amount="1000.00",
                user="PAYSWITCH",
                direction="DEBIT",
            )
        ],
    )

    assert result.results[0].final_status == "NOT_FOUND"
    assert result.results[0].bank is None
    assert result.summary["unmatched_bank_count"] == 1


def test_bank_credit_is_eligible_for_matching():
    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [
            bank(
                amount="1000.00",
                user="PAYSWITCH",
                direction="CREDIT",
            )
        ],
    )

    assert result.results[0].final_status == "MATCHED"


# ---------------------------------------------------------
# Sequential allocation
# ---------------------------------------------------------


def test_first_matching_bank_row_is_allocated_first():
    first_bank = bank(
        row=2,
        amount="1000.00",
        user="PAYSWITCH",
    )
    second_bank = bank(
        row=3,
        amount="1000.00",
        user="PAYSWITCH",
    )

    result = reconcile_transactions(
        [source(amount="1000.00", user="PAYSWITCH")],
        [first_bank, second_bank],
    )

    assert result.results[0].final_status == "MATCHED"
    assert result.results[0].bank.bank_row_number == 2
    assert result.summary["unmatched_bank_count"] == 1


def test_bank_transaction_cannot_be_reused():
    first_source = source(
        txn_id="SRC-1",
        amount="1000.00",
        user="PAYSWITCH",
    )
    second_source = source(
        txn_id="SRC-2",
        amount="1000.00",
        user="PAYSWITCH",
    )

    result = reconcile_transactions(
        [first_source, second_source],
        [
            bank(
                row=2,
                amount="1000.00",
                user="PAYSWITCH",
            )
        ],
    )

    assert [item.final_status for item in result.results] == [
        "MATCHED",
        "NOT_FOUND",
    ]

    assert result.results[0].bank.bank_row_number == 2
    assert result.results[1].bank is None
    assert result.summary["unmatched_bank_count"] == 0


def test_each_source_gets_a_different_bank_row():
    result = reconcile_transactions(
        [
            source(txn_id="SRC-1", amount="1000", user="PAYSWITCH"),
            source(txn_id="SRC-2", amount="1000", user="PAYSWITCH"),
        ],
        [
            bank(row=2, amount="1000", user="PAYSWITCH"),
            bank(row=3, amount="1000", user="PAYSWITCH"),
        ],
    )

    assert [item.final_status for item in result.results] == [
        "MATCHED",
        "MATCHED",
    ]

    matched_rows = [item.bank.bank_row_number for item in result.results]
    assert matched_rows == [2, 3]
    assert len(set(matched_rows)) == 2


# ---------------------------------------------------------
# Source eligibility
# ---------------------------------------------------------


def test_va_transaction_is_not_processed():
    result = reconcile_transactions(
        [
            source(
                source_type="va_transaction",
                status="success",
                amount="1000",
                user="PAYSWITCH",
            )
        ],
        [bank(amount="1000", user="PAYSWITCH")],
    )

    assert len(result.results) == 1

    va_result = result.results[0]

    assert va_result.final_status == "MISSING_REFERENCE"
    assert va_result.reference_status == "MISSING"
    assert va_result.exception_reason == "Source UTR/reference number is missing."
    assert result.summary["source_record_count"] == 1


def test_approved_fund_request_is_processed():
    result = reconcile_transactions(
        [
            source(
                source_type="fund_request",
                status="approved",
                amount="1000",
                user="PAYSWITCH",
            )
        ],
        [bank(amount="1000", user="PAYSWITCH")],
    )

    assert len(result.results) == 1
    assert result.results[0].final_status == "MATCHED"


def test_rejected_fund_request_is_not_matched():
    result = reconcile_transactions(
        [
            source(
                source_type="fund_request",
                status="rejected",
                amount="1000",
                user="PAYSWITCH",
            )
        ],
        [bank(amount="1000", user="PAYSWITCH")],
    )

    assert result.results[0].final_status == "EXCLUDED_BY_STATUS"
    assert result.results[0].bank is None


# ---------------------------------------------------------
# Missing data
# ---------------------------------------------------------


@pytest.mark.parametrize(
    "amount, user",
    [
        (None, "PAYSWITCH"),
        ("1000", None),
        ("1000", ""),
    ],
)
def test_missing_amount_or_party_name_does_not_match(amount, user):
    result = reconcile_transactions(
        [source(amount=amount, user=user)],
        [bank(amount="1000", user="PAYSWITCH")],
    )

    assert result.results[0].final_status == "NOT_FOUND"
    assert result.results[0].bank is None
