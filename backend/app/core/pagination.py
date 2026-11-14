"""Shared pagination query parameters for list APIs.

Clients such as Apidog may send blank query values (``?page=&limit=``) for
optional fields.  Blank values are treated exactly like an omitted parameter,
while any non-empty invalid value still fails normal integer and range
validation with HTTP 422.

Every list API must use these types so the project-wide standard stays in one
place::

    @app.get("/api/.../items")
    def list_items(
        page: PageParam = DEFAULT_PAGE,
        limit: LimitParam = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        ...

`page` defaults to 1 and must be at least 1.  `limit` defaults to 100 and must
be between 1 and 100 inclusive.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query
from pydantic import BeforeValidator

DEFAULT_PAGE = 1
DEFAULT_LIMIT = 100
MAX_LIMIT = 100


def _empty_string_to_default(default: int) -> BeforeValidator:
    def _convert(value: Any) -> Any:
        if value == "":
            return default
        return value

    return BeforeValidator(_convert)


PageParam = Annotated[
    int,
    Query(ge=1),
    _empty_string_to_default(DEFAULT_PAGE),
]

LimitParam = Annotated[
    int,
    Query(ge=1, le=MAX_LIMIT),
    _empty_string_to_default(DEFAULT_LIMIT),
]
