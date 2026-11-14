import re

# Extract references only when they appear in recognizable
# payment-system formats. Avoid treating arbitrary numbers
# in narration as UTRs.

REFERENCE_PATTERNS = [
    # IMPS/P2A/631418480656/HDFC/
    re.compile(
        r"\bIMPS\s*/\s*P2A\s*/\s*(\d{10,16})",
        re.IGNORECASE,
    ),
    # IMPS/631418480656
    re.compile(
        r"\bIMPS\s*/\s*(\d{10,16})",
        re.IGNORECASE,
    ),
    # UPI/123456789012 or UPI-123456789012
    re.compile(
        r"\bUPI\s*[/\-]\s*([A-Z0-9]{10,22})",
        re.IGNORECASE,
    ),
    # NEFT/AXISCN123456789012/...
    re.compile(
        r"\bNEFT\s*/\s*([A-Z0-9]{8,22})",
        re.IGNORECASE,
    ),
    # RTGS/AXISCN123456789012/...
    re.compile(
        r"\bRTGS\s*/\s*([A-Z0-9]{8,22})",
        re.IGNORECASE,
    ),
    # Explicit UTR/RRN/reference labels
    re.compile(
        r"\b(?:UTR|RRN|REF(?:ERENCE)?)" r"\s*[:/#\-]?\s*([A-Z0-9]{8,22})\b",
        re.IGNORECASE,
    ),
    # IMPS/PV/631430044624
    re.compile(
        r"\bIMPS\s*/\s*PV\s*/\s*(\d{10,16})",
        re.IGNORECASE,
    ),
]


def normalize_reference(value: str | None) -> str | None:
    """Normalize a reference without changing its identity."""

    if value is None:
        return None

    normalized = str(value).strip().upper()

    if not normalized:
        return None

    # Remove whitespace only. Keep meaningful separators.
    normalized = re.sub(r"\s+", "", normalized)

    return normalized


def extract_reference_candidates(
    narration: str | None,
) -> list[str]:
    """
    Extract possible UTR/RRN values from narration.

    Returns unique references in their order of appearance.
    Does not replace the bank's original reference number.
    """

    if narration is None:
        return []

    text = str(narration).strip()

    if not text:
        return []

    references = []
    seen = set()

    for pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            reference = normalize_reference(match.group(1))

            if reference and reference not in seen:
                seen.add(reference)
                references.append(reference)

    return references
