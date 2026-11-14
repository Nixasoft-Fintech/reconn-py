from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable

from app.core.normalizer import normalize_reference
from app.models.bank_transaction import BankTransaction
from app.models.reconciliation import MatchResult, ReconciliationOutput
from app.models.transaction import SourceTransaction

FUND_REQUEST_SOURCE_TYPE = "fund_request"
VA_TRANSACTION_SOURCE_TYPE = "va_transaction"

FUND_REQUEST_ALLOWED_STATUS = "approved"
VA_TRANSACTION_ALLOWED_STATUS = "success"

# These are legal/company suffixes, not business identity words.
LEGAL_SUFFIXES = {
    "PVT",
    "PRIVATE",
    "LTD",
    "LIMITED",
    "LLP",
    "LLC",
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
}

NAME_SIMILARITY_THRESHOLD = 0.86


@dataclass(slots=True)
class MatchingConfig:
    name_similarity_threshold: float = NAME_SIMILARITY_THRESHOLD
    date_tolerance_days: int | None = None
    min_partial_reference_length: int = 12
    min_partial_reference_digits: int = 8


@dataclass(slots=True)
class _VAProposal:
    source_index: int
    bank_index: int | None
    status: str
    reference_status: str
    amount_status: str
    user_status: str
    reason: str | None = None


def _normalize_name(value: str | None) -> list[str]:
    """Normalize a party name into meaningful tokens."""
    if not value:
        return []

    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper())
    return [token for token in normalized.split() if token not in LEGAL_SUFFIXES]


def _token_similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def names_match(
    first: str | None,
    second: str | None,
    threshold: float = NAME_SIMILARITY_THRESHOLD,
) -> bool:
    """
    Compare business names without depending on word order.

    Supports spacing differences, minor typos, missing first characters,
    extra business words, and legal suffix differences.
    """
    first_tokens = _normalize_name(first)
    second_tokens = _normalize_name(second)

    if not first_tokens or not second_tokens:
        return False

    first_joined = "".join(first_tokens)
    second_joined = "".join(second_tokens)

    if first_joined == second_joined:
        return True

    # Handle concatenated bank names with omitted legal suffixes.
    shorter_name, longer_name = sorted(
        [first_joined, second_joined],
        key=len,
    )

    if len(shorter_name) >= 8 and longer_name.startswith(shorter_name):
        return True

    # Keep your existing token-based matching below.
    shorter, longer = (
        (first_tokens, second_tokens)
        if len(first_tokens) <= len(second_tokens)
        else (second_tokens, first_tokens)
    )

    available = list(longer)

    for token in shorter:
        if not available:
            return False

        best_index = max(
            range(len(available)),
            key=lambda index: _token_similarity(token, available[index]),
        )
        score = _token_similarity(token, available[best_index])

        if score < threshold:
            return False

        available.pop(best_index)

    return True


def _is_bank_credit(bank: BankTransaction) -> bool:
    """Identify credit rows; debits and unknown directions are excluded."""
    direction = (bank.direction or "").strip().upper()

    credit_values = {"CREDIT", "CR", "C", "DEPOSIT", "INFLOW", "MONEY IN", "CREDITED"}
    debit_values = {"DEBIT", "DR", "D", "WITHDRAWAL", "OUTFLOW", "MONEY OUT", "DEBITED"}

    if direction in credit_values:
        return True

    if direction in debit_values:
        return False

    if bank.signed_amount is not None:
        return bank.signed_amount > Decimal("0")

    return False


def _source_references(source: SourceTransaction) -> set[str]:
    """Collect all available source reference identifiers."""
    values = (source.utr, source.reference_number)
    return {
        normalized
        for value in values
        if value and (normalized := normalize_reference(value))
    }


def _bank_references(bank: BankTransaction) -> set[str]:
    """Collect all available bank reference identifiers."""
    values = [
        bank.utr,
        bank.reference_number,
        *(bank.extracted_references or []),
    ]
    return {
        normalized
        for value in values
        if value and (normalized := normalize_reference(value))
    }


def _comparison_reference(value: str) -> str:
    """Remove punctuation and spaces for partial-reference comparison."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _is_strong_partial_match(
    source_reference: str,
    bank_reference: str,
    config: MatchingConfig,
) -> bool:
    """Check conservative partial-reference containment."""
    source_key = _comparison_reference(source_reference)
    bank_key = _comparison_reference(bank_reference)

    if not source_key or not bank_key:
        return False

    # Punctuation-only differences are equivalent references.
    if source_key == bank_key:
        return source_reference != bank_reference

    shorter, longer = sorted((source_key, bank_key), key=len)

    if len(shorter) < config.min_partial_reference_length:
        return False

    digit_count = sum(character.isdigit() for character in shorter)
    if digit_count < config.min_partial_reference_digits:
        return False

    return shorter in longer


def _eligible_va(source: SourceTransaction) -> bool:
    return (
        source.source_type or ""
    ).strip().casefold() == VA_TRANSACTION_SOURCE_TYPE and (
        source.status or ""
    ).strip().casefold() == VA_TRANSACTION_ALLOWED_STATUS


def _find_partial_candidates(
    source_references: set[str],
    bank: list[BankTransaction],
    config: MatchingConfig,
) -> list[int]:
    """Find bank rows with a strong partial-reference relationship."""
    candidates: list[int] = []

    for bank_index, bank_item in enumerate(bank):
        bank_references = _bank_references(bank_item)
        if any(
            _is_strong_partial_match(source_ref, bank_ref, config)
            for source_ref in source_references
            for bank_ref in bank_references
        ):
            candidates.append(bank_index)

    return candidates


def _build_va_proposal(
    source_index: int,
    source: SourceTransaction,
    bank: list[BankTransaction],
    bank_by_reference: dict[str, list[int]],
    config: MatchingConfig,
) -> _VAProposal:
    if not _eligible_va(source):
        return _VAProposal(
            source_index,
            None,
            "EXCLUDED_BY_STATUS",
            "NOT_CHECKED",
            "NOT_CHECKED",
            "NOT_USED",
            "VA Transaction status is not success.",
        )

    if source.amount is None or source.source_transaction_id is None:
        return _VAProposal(
            source_index,
            None,
            "INVALID_SOURCE_RECORD",
            "NOT_CHECKED",
            "NOT_CHECKED",
            "NOT_USED",
            "Source transaction ID or amount is missing/invalid.",
        )

    references = _source_references(source)
    if not references:
        return _VAProposal(
            source_index,
            None,
            "MISSING_REFERENCE",
            "MISSING",
            "NOT_CHECKED",
            "NOT_USED",
            "Source UTR/reference number is missing.",
        )

    # Exact reference candidates have priority.
    candidate_indices = sorted(
        {
            index
            for reference in references
            for index in bank_by_reference.get(reference, [])
        }
    )
    reference_status = "MATCH"

    # If no exact reference exists, try strong partial references.
    if not candidate_indices:
        candidate_indices = _find_partial_candidates(references, bank, config)
        reference_status = "PARTIAL_MATCH"

    if not candidate_indices:
        return _VAProposal(
            source_index,
            None,
            "NOT_FOUND",
            "NOT_FOUND",
            "NOT_CHECKED",
            "NOT_USED",
            "No exact or strong partial bank reference match found.",
        )

    valid_amount_candidates = [
        index for index in candidate_indices if bank[index].amount is not None
    ]
    if not valid_amount_candidates:
        return _VAProposal(
            source_index,
            candidate_indices[0] if len(candidate_indices) == 1 else None,
            "INVALID_BANK_RECORD",
            reference_status,
            "INVALID_BANK_AMOUNT",
            "NOT_USED",
            "Reference matched, but bank amount is missing or invalid.",
        )

    amount_matches = [
        index
        for index in valid_amount_candidates
        if bank[index].amount == source.amount
    ]

    if reference_status == "PARTIAL_MATCH" and len(candidate_indices) > 1:
        return _VAProposal(
            source_index,
            None,
            "AMBIGUOUS",
            "PARTIAL_MATCH",
            "MATCH" if amount_matches else "MISMATCH",
            "NOT_USED",
            "Multiple bank transactions have plausible partial reference matches. Manual review is required.",
        )

    if len(amount_matches) > 1:
        return _VAProposal(
            source_index,
            None,
            "AMBIGUOUS",
            reference_status,
            "MATCH",
            "NOT_USED",
            "Multiple bank transactions match the reference and amount.",
        )

    if len(amount_matches) == 1:
        return _VAProposal(
            source_index,
            amount_matches[0],
            "MATCHED",
            reference_status,
            "MATCH",
            "NOT_USED",
            (
                None
                if reference_status == "MATCH"
                else "Strong partial reference and exact amount matched."
            ),
        )

    if len(valid_amount_candidates) == 1:
        return _VAProposal(
            source_index,
            valid_amount_candidates[0],
            "AMOUNT_MISMATCH",
            reference_status,
            "MISMATCH",
            "NOT_USED",
            "Reference matched, but amount is not exactly equal.",
        )

    return _VAProposal(
        source_index,
        None,
        "AMBIGUOUS",
        reference_status,
        "MISMATCH",
        "NOT_USED",
        "Multiple reference candidates exist, but none has the exact amount.",
    )


def _reconcile_fund_requests(
    sources: list[SourceTransaction],
    bank: list[BankTransaction],
    config: MatchingConfig,
    allocated: set[int],
) -> list[MatchResult]:
    """Fund Request matching: approved + exact amount + flexible party name."""
    results: list[MatchResult] = []

    available_credit_indices = [
        index for index, item in enumerate(bank) if _is_bank_credit(item)
    ]

    for source in sources:
        if (source.source_type or "").strip().casefold() != FUND_REQUEST_SOURCE_TYPE:
            continue

        if (source.status or "").strip().casefold() != FUND_REQUEST_ALLOWED_STATUS:
            results.append(
                MatchResult(
                    source=source,
                    bank=None,
                    final_status="EXCLUDED_BY_STATUS",
                    reference_status="NOT_USED",
                    amount_status="NOT_CHECKED",
                    user_status="NOT_CHECKED",
                    date_status="NOT_USED",
                    exception_reason="Fund Request status is not approved.",
                )
            )
            continue

        if source.amount is None or not source.user:
            results.append(
                MatchResult(
                    source=source,
                    bank=None,
                    final_status="NOT_FOUND",
                    reference_status="NOT_USED",
                    amount_status="NOT_AVAILABLE",
                    user_status="NOT_AVAILABLE",
                    date_status="NOT_USED",
                    exception_reason="Fund Request amount or party name is missing.",
                )
            )
            continue

        selected_index: int | None = None

        # Preserve bank statement order and choose the first available credit.
        for bank_index in available_credit_indices:
            if bank_index in allocated:
                continue

            bank_item = bank[bank_index]
            if bank_item.amount is None or bank_item.user is None:
                continue

            if source.amount != bank_item.amount:
                continue

            if not names_match(
                source.user,
                bank_item.user,
                config.name_similarity_threshold,
            ):
                continue

            selected_index = bank_index
            break

        if selected_index is None:
            results.append(
                MatchResult(
                    source=source,
                    bank=None,
                    final_status="NOT_FOUND",
                    reference_status="NOT_USED",
                    amount_status="NOT_FOUND",
                    user_status="NOT_FOUND",
                    date_status="NOT_USED",
                    exception_reason=(
                        "No available bank credit matched both "
                        "the exact amount and party name."
                    ),
                )
            )
            continue

        allocated.add(selected_index)
        results.append(
            MatchResult(
                source=source,
                bank=bank[selected_index],
                final_status="MATCHED",
                reference_status="NOT_USED",
                amount_status="MATCH",
                user_status="MATCH",
                date_status="NOT_USED",
                exception_reason=(
                    "Matched by exact amount and flexible party name. "
                    "Allocated sequentially using bank statement order."
                ),
            )
        )

    return results


def _reconcile_va_transactions(
    sources: list[SourceTransaction],
    bank: list[BankTransaction],
    config: MatchingConfig,
    allocated: set[int],
) -> list[MatchResult]:
    """Restore VA reference-based reconciliation from the previous engine."""
    va_sources = [
        source
        for source in sources
        if (source.source_type or "").strip().casefold() == VA_TRANSACTION_SOURCE_TYPE
    ]

    bank_by_reference: dict[str, list[int]] = defaultdict(list)
    for bank_index, item in enumerate(bank):
        for reference in _bank_references(item):
            bank_by_reference[reference].append(bank_index)

    # Detect duplicate VA source records using source type, references and amount.
    duplicate_groups: dict[tuple[str, tuple[str, ...], Decimal], list[int]] = (
        defaultdict(list)
    )

    for index, source in enumerate(va_sources):
        refs = tuple(sorted(_source_references(source)))
        if _eligible_va(source) and refs and source.amount is not None:
            duplicate_groups[(source.source_type, refs, source.amount)].append(index)

    duplicate_indices = {
        index
        for indices in duplicate_groups.values()
        if len(indices) > 1
        for index in indices
    }

    proposals: list[_VAProposal] = []
    for index, source in enumerate(va_sources):
        if index in duplicate_indices:
            proposals.append(
                _VAProposal(
                    index,
                    None,
                    "DUPLICATE_REFERENCE",
                    "DUPLICATE",
                    "NOT_CHECKED",
                    "NOT_USED",
                    "Duplicate source records have the same source type, reference and amount.",
                )
            )
        else:
            proposals.append(
                _build_va_proposal(index, source, bank, bank_by_reference, config)
            )

    # Detect cases where multiple VA sources propose the same bank row.
    proposed_bank_users: dict[int, list[int]] = defaultdict(list)
    candidate_statuses = {"MATCHED", "AMOUNT_MISMATCH"}

    for proposal in proposals:
        if proposal.bank_index is not None and proposal.status in candidate_statuses:
            proposed_bank_users[proposal.bank_index].append(proposal.source_index)

    contested = {
        source_index
        for source_indices in proposed_bank_users.values()
        if len(source_indices) > 1
        for source_index in source_indices
    }

    results: list[MatchResult] = []

    for proposal in proposals:
        source = va_sources[proposal.source_index]
        bank_item = (
            bank[proposal.bank_index] if proposal.bank_index is not None else None
        )
        status = proposal.status
        reason = proposal.reason
        reference_status = proposal.reference_status

        if proposal.source_index in contested:
            status = "AMBIGUOUS"
            reason = (
                "This bank row matches multiple source records; "
                "no allocation was made."
            )
            bank_item = None
            reference_status = "MULTIPLE_SOURCE_CANDIDATES"

        elif proposal.bank_index is not None and proposal.status in candidate_statuses:
            if proposal.bank_index in allocated:
                status = "AMBIGUOUS"
                reason = (
                    "This bank row has already been allocated "
                    "to another source record."
                )
                bank_item = None
                reference_status = "ALREADY_ALLOCATED"
            else:
                # Preserve previous behavior: a reference candidate with an
                # amount mismatch is also reserved from being reused.
                allocated.add(proposal.bank_index)

        results.append(
            MatchResult(
                source=source,
                bank=bank_item,
                final_status=status,
                reference_status=reference_status,
                amount_status=proposal.amount_status,
                user_status=proposal.user_status,
                date_status="NOT_USED",
                exception_reason=reason,
            )
        )

    return results


def _build_summary(
    results: list[MatchResult],
    bank: list[BankTransaction],
    allocated: set[int],
) -> dict[str, object]:
    status_counts = Counter(result.final_status for result in results)

    matched_amount = sum(
        (
            result.source.amount or Decimal("0")
            for result in results
            if result.final_status == "MATCHED"
        ),
        Decimal("0"),
    )

    unmatched_bank = [item for index, item in enumerate(bank) if index not in allocated]

    by_source: dict[str, dict[str, object]] = {}
    source_types = sorted(
        {(result.source.source_type or "").strip().casefold() for result in results}
    )

    for source_type in source_types:
        source_results = [
            result
            for result in results
            if (result.source.source_type or "").strip().casefold() == source_type
        ]
        source_counts = Counter(result.final_status for result in source_results)
        source_matched_amount = sum(
            (
                result.source.amount or Decimal("0")
                for result in source_results
                if result.final_status == "MATCHED"
            ),
            Decimal("0"),
        )
        source_eligible_amount = sum(
            (
                result.source.amount or Decimal("0")
                for result in source_results
                if (
                    result.final_status != "EXCLUDED_BY_STATUS"
                    and result.source.amount is not None
                )
            ),
            Decimal("0"),
        )

        by_source[source_type] = {
            "record_count": len(source_results),
            "status_counts": dict(source_counts),
            "matched_count": source_counts.get("MATCHED", 0),
            "matched_amount": str(source_matched_amount),
            "eligible_source_amount": str(source_eligible_amount),
        }

    return {
        "source_record_count": len(results),
        "bank_record_count": len(bank),
        "status_counts": dict(status_counts),
        "by_source": by_source,
        "matched_count": status_counts.get("MATCHED", 0),
        "matched_amount": str(matched_amount),
        "unmatched_bank_count": len(unmatched_bank),
        "unmatched_bank_amount": str(
            sum(
                (item.amount or Decimal("0") for item in unmatched_bank),
                Decimal("0"),
            )
        ),
    }


def reconcile_transactions(
    source_transactions: Iterable[SourceTransaction],
    bank_transactions: Iterable[BankTransaction],
    config: MatchingConfig | None = None,
) -> ReconciliationOutput:
    """
    Reconcile Fund Request and VA Transaction reports against bank statements.

    Fund Request:
      - approved status
      - exact amount
      - flexible party-name match
      - first available bank credit in statement order

    VA Transaction:
      - success status
      - exact or strong partial reference
      - exact amount
      - duplicate and ambiguity detection

    A bank row can be allocated to at most one source record across both flows.
    """
    config = config or MatchingConfig()
    sources = list(source_transactions)
    bank = list(bank_transactions)

    allocated: set[int] = set()

    # The API supplies Fund Request records before VA records. Keep the flows
    # separate while sharing allocation state so a bank row cannot be reused.
    fund_results = _reconcile_fund_requests(sources, bank, config, allocated)
    va_results = _reconcile_va_transactions(sources, bank, config, allocated)

    results = fund_results + va_results
    summary = _build_summary(results, bank, allocated)

    unmatched_bank = [item for index, item in enumerate(bank) if index not in allocated]

    return ReconciliationOutput(
        results=results,
        bank_transactions=bank,
        unmatched_bank_rows=unmatched_bank,
        summary=summary,
    )
