from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CompanyCreate(BaseModel):
    """Request body for creating a reusable company record."""

    name: str = Field(min_length=1, max_length=255)
    alias: str | None = Field(default=None, max_length=255)

    @field_validator("name", "alias", mode="before")
    @classmethod
    def _strip(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("alias")
    @classmethod
    def _blank_alias_is_none(cls, value: str | None) -> str | None:
        return value or None
