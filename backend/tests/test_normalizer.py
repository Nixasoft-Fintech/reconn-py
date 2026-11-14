from decimal import Decimal

from app.core.normalizer import normalize_reference, normalize_user, parse_amount, parse_datetime


def test_reference_normalization_preserves_meaningful_characters():
    assert normalize_reference("  cnrbr52026 / 123  ") == "CNRBR52026/123"
    assert normalize_reference("00123") == "00123"
    assert normalize_reference("") is None


def test_user_normalization():
    assert normalize_user("  NixaSoft   Fintech Pvt Ltd ") == "NIXASOFT FINTECH PVT LTD"


def test_amount_parsing():
    assert parse_amount("₹1,23,456.78") == Decimal("123456.78")
    assert parse_amount("(1,000.50)") == Decimal("-1000.50")
    assert parse_amount("bad-value") is None


def test_date_parsing_source_format():
    parsed = parse_datetime("10-11-2026 22:26:01")
    assert parsed is not None
    assert parsed.isoformat() == "2026-11-10T22:26:01"
