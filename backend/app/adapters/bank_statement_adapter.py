from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd

from app.core.normalizer import (
    clean_text,
    normalize_reference,
    normalize_user,
    parse_amount,
    parse_datetime,
)
from app.models.bank_transaction import BankTransaction
from app.core.reference_extractor import extract_reference_candidates
from app.adapters.bank_profile_detector import (
    BankProfile,
    detect_bank_profile,
)
from app.core.file_reader import BankStatementReadResult

# Common narration words and metadata markers that are not party names.
NARRATION_STOPWORDS = {
    "RECD",
    "BILL",
    "NEFT",
    "IMPS",
    "RTGS",
    "UPI",
    "IFT",
    "FT",
    "CMS",
    "NACH",
    "ECS",
    "ACH",
    "CR",
    "CREDIT",
    "CREDITED",
    "DR",
    "DEBIT",
    "TRANSFER",
    "PAYMENT",
    "TRF",
    "BY",
    "TO",
    "FROM",
    "BANK",
    "REF",
    "REFERENCE",
    "UTR",
    "INB",
    "P2A",
    "TPAR",
    "TPARTY",
    "NONE",
    "SELF",
    "CASH",
    "A2AINT01",
}

# Bank names commonly embedded after the beneficiary in narrations.
BANK_NAME_MARKERS = {
    "CANARA",
    "PUNJAB",
    "PUNJABNA",
    "HDFC",
    "ICICI",
    "KOTAK",
    "AXIS",
    "SBI",
    "IDFC",
    "INDUSIND",
    "FEDERAL",
    "BARODA",
    "BOB",
    "YESBANK",
    "YES",
    "UNION",
    "RBL",
    "BANDHAN",
    "AU",
    "CITI",
    "STANDARD",
}


def _clean_party_segment(segment: str) -> str | None:
    """Clean one narration segment without combining unrelated metadata."""
    segment = re.sub(r"[^A-Z0-9&. ]+", " ", segment.upper())
    tokens = segment.split()
    cleaned: list[str] = []

    for token in tokens:
        token = token.strip(".")
        if not token:
            continue
        # Keep short alphanumeric beneficiary names such as I4TICKET,
        # but discard numeric IDs and long bank/reference strings.
        if token.isdigit() or (
            any(char.isdigit() for char in token) and len(token) > 12
        ):
            continue
        if token in NARRATION_STOPWORDS:
            continue
        if token in BANK_NAME_MARKERS:
            break
        cleaned.append(token)

    # Avoid returning narration codes or one-character fragments.
    if not cleaned or all(len(token) <= 1 for token in cleaned):
        return None
    return " ".join(cleaned)


def extract_party_from_narration(narration: str | None) -> str | None:
    """
    Extract a likely beneficiary name from a bank narration.

    Prefer a distinct narration segment (e.g. the beneficiary between
    slash-delimited bank metadata), rather than merging every word in the
    narration. Numeric references, transaction codes, bank names and
    trailing markers are discarded.
    """
    if narration is None or not str(narration).strip():
        return None

    text = str(narration).strip().upper()

    # Bank statements commonly separate transaction type, reference,
    # beneficiary, and bank metadata with slashes or hyphens.
    segments = [
        part.strip()
        for part in re.split(r"[/|,:;*]+|\\s+-\\s+", text)
        if part and part.strip()
    ]

    # Remove the transaction prefix when it is attached to the narration.
    if segments and segments[0] == "RECD":
        segments = segments[1:]

    candidates: list[str] = []
    for segment in segments:
        candidate = _clean_party_segment(segment)
        if candidate:
            # Ignore generic transaction labels and tiny fragments.
            if candidate not in NARRATION_STOPWORDS:
                candidates.append(candidate)

    if candidates:
        # The beneficiary is usually the first meaningful text segment
        # after the transaction code/reference. Choosing the first avoids
        # appending bank names or trailing channel markers.
        return candidates[0]

    # Fallback for narrations without useful delimiters.
    return _clean_party_segment(text)


class BankMappingError(ValueError):
    """Raised when bank column mappings are missing or invalid."""


@dataclass(frozen=True, slots=True)
class BankColumnMapping:
    utr: str | None = None
    reference_number: str | None = None

    amount: str | None = None
    debit_amount: str | None = None
    credit_amount: str | None = None

    transaction_type: str | None = None
    transaction_date: str | None = None
    value_date: str | None = None

    user: str | None = None
    beneficiary_name: str | None = None
    narration: str | None = None

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> BankColumnMapping:

        allowed = set(cls.__dataclass_fields__)
        unknown = set(payload) - allowed

        if unknown:
            raise BankMappingError(
                f"Unknown mapping fields: {', '.join(sorted(unknown))}"
            )

        values = {
            key: (
                str(value).strip() if value is not None and str(value).strip() else None
            )
            for key, value in payload.items()
        }

        return cls(**values)

    def validate(self, columns: list[str]) -> None:
        available = set(columns)

        if not self.utr and not self.reference_number:
            raise BankMappingError(
                "Map at least one bank reference field: UTR or Reference Number."
            )

        has_debit = bool(self.debit_amount)
        has_credit = bool(self.credit_amount)

        if self.amount and (has_debit or has_credit):
            raise BankMappingError(
                "Map either one Amount column or Debit/Credit columns, not both."
            )

        if not self.amount and not (has_debit and has_credit):
            raise BankMappingError(
                "Map an Amount column or both Debit Amount and Credit Amount columns."
            )

        if has_debit != has_credit:
            raise BankMappingError(
                "For separate amount columns, map both Debit Amount and Credit Amount."
            )

        configured_columns = [
            value
            for value in (
                self.utr,
                self.reference_number,
                self.amount,
                self.debit_amount,
                self.credit_amount,
                self.transaction_type,
                self.transaction_date,
                self.value_date,
                self.user,
                self.beneficiary_name,
                self.narration,
            )
            if value
        ]

        missing = [column for column in configured_columns if column not in available]

        if missing:
            raise BankMappingError(
                "Mapped bank columns are not present in the file: " + ", ".join(missing)
            )


class BankStatementAdapter:

    def __init__(
        self,
        mapping: BankColumnMapping,
        *,
        bank_name: str | None = None,
        bank_format: str | None = None,
        account_number: str | None = None,
        account_holder: str | None = None,
        source_filename: str | None = None,
    ):
        self.mapping = mapping
        self.bank_name = bank_name
        self.bank_format = bank_format
        self.account_number = account_number
        self.account_holder = account_holder
        self.source_filename = source_filename

    def normalize(
        self,
        frame: pd.DataFrame,
    ) -> list[BankTransaction]:

        columns = [str(column) for column in frame.columns]
        self.mapping.validate(columns)

        records: list[BankTransaction] = []

        for position, (index, row) in enumerate(
            frame.iterrows(),
            start=2,
        ):

            # Preserve original physical CSV/Excel row number.
            if frame.index.name == "source_row_number":
                row_number = int(index)
            else:
                row_number = position

            raw = {str(key): value for key, value in row.items()}

            # HDFC statements contain headers, summaries and footers
            # mixed with transaction rows.
            if self.bank_format == "hdfc":
                date_column = self.mapping.transaction_date

                if date_column:
                    raw_date = row.get(date_column)

                    # Reject empty or non-transaction rows.
                    if pd.isna(raw_date):
                        continue

                    date_text = str(raw_date).strip()

                    # HDFC transaction dates use DD/MM/YY or DD/MM/YYYY.
                    if not re.fullmatch(
                        r"\d{2}/\d{2}/\d{2,4}",
                        date_text,
                    ):
                        continue

            # Original UTR.
            utr = (
                normalize_reference(row.get(self.mapping.utr))
                if self.mapping.utr
                else None
            )

            # Original bank reference.
            reference = (
                normalize_reference(row.get(self.mapping.reference_number))
                if self.mapping.reference_number
                else None
            )

            # Narration.
            narration = (
                clean_text(row.get(self.mapping.narration))
                if self.mapping.narration
                else None
            )

            # Extract possible references from narration.
            extracted_references = extract_reference_candidates(narration)

            # Transaction direction.
            direction = (
                clean_text(row.get(self.mapping.transaction_type))
                if self.mapping.transaction_type
                else None
            )

            signed_amount: Decimal | None = None

            # Single amount column.
            if self.mapping.amount:

                signed_amount = parse_amount(row.get(self.mapping.amount))

            # Separate debit and credit columns.
            else:

                debit = parse_amount(row.get(self.mapping.debit_amount))

                credit = parse_amount(row.get(self.mapping.credit_amount))

                debit_present = debit is not None and debit != 0

                credit_present = credit is not None and credit != 0

                if debit_present and credit_present:
                    raise BankMappingError(
                        f"Bank row {row_number} has both debit " "and credit values."
                    )

                if debit_present:
                    signed_amount = debit
                    direction = direction or "DEBIT"

                elif credit_present:
                    signed_amount = credit
                    direction = direction or "CREDIT"

                elif debit is not None or credit is not None:
                    signed_amount = Decimal("0")

            amount = abs(signed_amount) if signed_amount is not None else None

            # Party name: an explicitly mapped beneficiary column is the
            # strongest source. Otherwise prefer a clean party extracted
            # from narration over generic "user" columns, which can contain
            # channel/bank metadata in some statement formats.
            beneficiary_value = (
                row.get(self.mapping.beneficiary_name)
                if self.mapping.beneficiary_name
                else None
            )
            if beneficiary_value is not None and pd.isna(beneficiary_value):
                beneficiary_value = None

            narration_party = extract_party_from_narration(narration)

            mapped_user = row.get(self.mapping.user) if self.mapping.user else None
            if mapped_user is not None and pd.isna(mapped_user):
                mapped_user = None

            user_value = beneficiary_value or narration_party or mapped_user

            # Transaction date.
            transaction_date = (
                parse_datetime(row.get(self.mapping.transaction_date))
                if self.mapping.transaction_date
                else None
            )

            # Value date.
            value_date = (
                parse_datetime(row.get(self.mapping.value_date))
                if self.mapping.value_date
                else None
            )

            # Create common bank transaction object.
            transaction = BankTransaction(
                bank_row_number=row_number,
                utr=utr,
                reference_number=reference,
                amount=amount,
                user=normalize_user(user_value),
                transaction_date=transaction_date,
                narration=narration,
                direction=direction,
                signed_amount=signed_amount,
                bank_name=self.bank_name,
                bank_account_number=self.account_number,
                account_holder=self.account_holder,
                bank_format=self.bank_format,
                source_filename=self.source_filename,
                value_date=value_date,
                extracted_references=extracted_references,
                raw_data=raw,
            )

            records.append(transaction)

        return records


def normalize_bank_statement(
    read_result: BankStatementReadResult,
) -> tuple[BankProfile, list[BankTransaction]]:
    """
    Automatically detect the bank format and normalize its transactions.

    Returns:
        Detected bank profile and normalized transactions.
    """

    frame = read_result.frame

    if frame.empty:
        raise BankMappingError(
            f"Bank statement '{read_result.source_file}' contains no transactions."
        )

    # Detect bank format.
    profile = detect_bank_profile(frame)

    # Keep only mapping fields supported by BankColumnMapping.
    allowed_fields = set(BankColumnMapping.__dataclass_fields__)

    mapping_payload = {
        key: value for key, value in profile.mapping.items() if key in allowed_fields
    }

    mapping = BankColumnMapping.from_dict(mapping_payload)

    # Create adapter with account and file metadata.
    adapter = BankStatementAdapter(
        mapping,
        bank_name=profile.bank_name,
        bank_format=profile.bank_format,
        account_number=read_result.metadata.get("account_number"),
        account_holder=read_result.metadata.get("account_holder"),
        source_filename=read_result.source_file,
    )

    transactions = adapter.normalize(frame)

    return profile, transactions
