from __future__ import annotations

import uuid

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TransactionType = Literal["fund_request", "va_transaction", "bank"]


class TransactionReference(BaseModel):
    """Identity of one persisted transaction.

    Source records are identified by their source type and transaction id.

    Unmatched bank records use ``transaction_id``, the
    ``UnmatchedBankTransaction.id`` exposed as ``bank_transaction_id`` by the
    unmatched bank listing.  Already-linked bank rows have no unmatched-bank
    id, so their bank side is derived from the selected source record's
    existing bank linkage; a bank reference for such a row carries only
    ``transaction_type``.
    """

    transaction_type: TransactionType

    transaction_id: str | None = Field(default=None, max_length=255)

    @field_validator("transaction_id", mode="before")
    @classmethod
    def _normalize(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> "TransactionReference":
        if self.transaction_type == "bank":
            if self.transaction_id is None:
                return self

            if not self.transaction_id.isdigit() or int(
                self.transaction_id
            ) < 1:
                raise ValueError(
                    "transaction_id must be the positive numeric id of an "
                    "unmatched bank transaction."
                )
            return self

        if not self.transaction_id:
            raise ValueError(
                "transaction_id is required for a fund_request or "
                "va_transaction."
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

        if primary_is_bank and not self.primary.transaction_id:
            raise ValueError(
                "transaction_id is required when the bank transaction is the "
                "primary record."
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
