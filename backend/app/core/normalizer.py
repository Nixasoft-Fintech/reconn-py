from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


def clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_reference(value: Any) -> str | None:
    """Normalize case and surrounding/internal whitespace only.

    Punctuation and digits are preserved because they may be meaningful in
    bank references. No UTR-format assumptions are made here.
    """
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", "", text).upper()


def normalize_user(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).upper()


def parse_amount(value: Any) -> Decimal | None:
    text = clean_text(value)
    if text is None:
        return None

    text = text.replace("₹", "").replace("INR", "").replace("inr", "")
    text = text.replace(",", "").strip()
    is_parenthesized_negative = text.startswith("(") and text.endswith(")")
    if is_parenthesized_negative:
        text = text[1:-1].strip()

    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return -amount if is_parenthesized_negative else amount


def parse_datetime(value: Any) -> datetime | None:
    text = clean_text(value)
    if text is None:
        return None

    # Explicitly support the source report's DD-MM-YYYY HH:MM:SS format first.
    for fmt in (
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def normalize_status(value: Any) -> str | None:
    text = clean_text(value)
    return text.casefold() if text else None
