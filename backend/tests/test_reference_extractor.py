from app.core.reference_extractor import (
    extract_reference_candidates,
    normalize_reference,
)


def test_extract_imps_p2a_reference():
    narration = "IMPS/P2A/631418480656/HDFC/"

    result = extract_reference_candidates(narration)

    assert result == ["631418480656"]


def test_extract_neft_reference():
    narration = "NEFT/AXISCN123456789012/NEXASOFT"

    result = extract_reference_candidates(narration)

    assert result == ["AXISCN123456789012"]


def test_extract_rtgs_reference():
    narration = "RTGS/AXISCN123456789012/NEXASOFT"

    result = extract_reference_candidates(narration)

    assert result == ["AXISCN123456789012"]


def test_extract_upi_reference():
    narration = "UPI/123456789012/PAYMENT"

    result = extract_reference_candidates(narration)

    assert result == ["123456789012"]


def test_extract_explicit_utr():
    narration = "PAYMENT RECEIVED UTR: ABCD123456789012"

    result = extract_reference_candidates(narration)

    assert result == ["ABCD123456789012"]


def test_no_reference_for_unrelated_narration():
    narration = "Monthly account maintenance charges"

    result = extract_reference_candidates(narration)

    assert result == []


def test_empty_narration():
    assert extract_reference_candidates(None) == []
    assert extract_reference_candidates("") == []


def test_normalize_reference():
    assert normalize_reference("  abc 123  ") == "ABC123"
    assert normalize_reference(None) is None


def test_extract_imps_pv_utr():
    narration = (
        "IMPS/PV/631430044624/8130823945/" "VA/DIGISPHERE/HDFC BAN/IMPS NEXASOF/NEXA/"
    )

    assert extract_reference_candidates(narration) == ["631430044624"]
