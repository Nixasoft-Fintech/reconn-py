from __future__ import annotations

import uuid

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TransactionType = Literal["fund_request", "va_transaction", "bank"]


class TransactionReference(BaseModel):
    """Identity of one persisted transaction.

    Source records are identified by their source type and transaction id.
    Bank records use the composite identity
    ``(bank_source_filename, bank_row_number)``.
    """

    transaction_type: TransactionType

    transaction_id: str | None = Field(default=None, max_length=255)

    bank_source_filename: str | None = Field(default=None, max_length=255)
    bank_row_number: int | None = Field(default=None, ge=1)

    @field_validator("transaction_id", "bank_source_filename", mode="before")
    @classmethod
    def _strip(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> "TransactionReference":
        if self.transaction_type == "bank":
            if not self.bank_source_filename:
                raise ValueError(
                    "bank_source_filename is required for a bank transaction."
                )
            if self.bank_row_number is None:
                raise ValueError(
                    "bank_row_number is required for a bank transaction."
                )
            if self.transaction_id:
                raise ValueError(
                    "transaction_id must not be supplied for a bank transaction."
                )
            return self

        if not self.transaction_id:
            raise ValueError(
                "transaction_id is required for a fund_request or "
                "va_transaction."
            )
        if self.bank_source_filename or self.bank_row_number is not None:
            raise ValueError(
                "Bank identity fields must not be supplied for a fund_request "
                "or va_transaction."
            )
        return self


class ManualSettlementCreate(BaseModel):
    """Request body for settling one source record against one bank row."""

    primary: TransactionReference
    company_id: uuid.UUID
    counterpart: TransactionReference
    remarks: str | None = Field(default=None, max_length=2000)

    @field_validator("remarks", mode="before")
    @classmethod
    def _strip_remarks(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _validate_pair(self) -> "ManualSettlementCreate":
        primary_is_bank = self.primary.transaction_type == "bank"
        counterpart_is_bank = self.counterpart.transaction_type == "bank"

        if primary_is_bank == counterpart_is_bank:
            raise ValueError(
                "Exactly one of primary or counterpart must be a bank "
                "transaction."
            )

        return self


class ManualSettlementReversal(BaseModel):
    """Optional body for reversing an active manual settlement."""

    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value
