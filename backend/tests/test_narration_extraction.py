from app.adapters.bank_statement_adapter import extract_party_from_narration


def test_kotak_narration():
    result = extract_party_from_narration(
        "HDFCR52026111084124582 BRACE VENTURES PRIVATE LI"
    )
    assert result == "BRACE VENTURES PRIVATE LI"


def test_neft_narration():
    result = extract_party_from_narration("NEFT/123456789/PAYSWITCH INDIA PVT LTD")
    assert result == "PAYSWITCH INDIA PVT LTD"


def test_empty_narration():
    assert extract_party_from_narration(None) is None
