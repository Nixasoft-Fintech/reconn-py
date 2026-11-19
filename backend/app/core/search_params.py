"""Optional query parameters shared by the reconciliation list APIs.

Apidog clients send blank strings for optional filters.  Those are treated
exactly like an omitted filter, while non-empty invalid values keep FastAPI's
normal decimal/integer validation (HTTP 422).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import Query
from pydantic import BeforeValidator


def _blank_to_none(value: Any) -> Any:
    """Normalize an omitted or blank query value to ``None``."""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


TextFilterParam = Annotated[
    str | None,
    Query(max_length=255),
    BeforeValidator(_blank_to_none),
]

AmountFilterParam = Annotated[
    Decimal | None,
    BeforeValidator(_blank_to_none),
]

BankRowNumberFilterParam = Annotated[
    int | None,
    Query(ge=0),
    BeforeValidator(_blank_to_none),
]
