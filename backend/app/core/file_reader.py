from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin1")
BANK_DELIMITERS = (",", "\t", ";", "|")


class FileReadError(ValueError):
    """Raised when an input file cannot be read safely."""


@dataclass(slots=True)
class BankStatementReadResult:
    """A bank statement table plus metadata discovered before its header."""

    frame: pd.DataFrame
    source_file: str
    header_row_number: int
    metadata: dict[str, Any]
    sheet_name: str | None = None
    delimiter: str | None = None


# Header aliases used only to locate the transaction table. They do not map
# columns for reconciliation; a bank adapter still has to validate the fields.
_HEADER_GROUPS: dict[str, set[str]] = {
    "date": {
        "date",
        "transaction date",
        "transaction date dd mm yyyy",
        "transaction date time",
        "date time",
        "txn date",
        "value date",
    },
    "amount": {
        "amount",
        "amount inr",
        "transaction amount",
        "withdrawal amt",
        "withdrawal amt",
        "withdrawal amount",
        "deposit amt",
        "deposit amount",
        "debit",
        "credit",
        "debit amount",
        "credit amount",
    },
    "narration": {
        "narration",
        "narrations",
        "particulars",
        "description",
        "remarks",
        "payment narration",
        "transaction details",
    },
    "reference": {
        "chq ref no",
        "chq no",
        "cheque number",
        "bank reference",
        "reference number",
        "ref no",
        "utr",
        "utr number",
        "merchant ref",
        "ref id utr",
    },
    "direction": {"debit credit", "dr cr", "type", "transaction type"},
}


def _validate_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise FileReadError(f"Input file does not exist: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise FileReadError(
            f"Unsupported file type '{file_path.suffix}'. "
            f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return file_path


def read_tabular_file(path: str | Path) -> pd.DataFrame:
    """Read a conventional CSV/XLSX/XLS whose first row is the header.

    This is retained for the two source reports. Bank statements should use
    ``read_bank_statement_file`` because they often contain preamble rows.
    """
    file_path = _validate_file(path)
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        last_error: Exception | None = None
        for encoding in CSV_ENCODINGS:
            try:
                frame = pd.read_csv(
                    file_path,
                    dtype=str,
                    encoding=encoding,
                    sep=None,
                    engine="python",
                    keep_default_na=False,
                )
                return _clean_headers(frame)
            except UnicodeDecodeError as exc:
                last_error = exc
            except pd.errors.ParserError as exc:
                raise FileReadError(
                    f"Could not parse CSV '{file_path.name}': {exc}"
                ) from exc
        raise FileReadError(
            f"Could not decode CSV '{file_path.name}' with supported encodings."
        ) from last_error

    try:
        frame = pd.read_excel(file_path, dtype=str, keep_default_na=False)
    except ImportError as exc:
        raise FileReadError(
            "Excel support dependency is missing. Install requirements.txt."
        ) from exc
    except Exception as exc:
        raise FileReadError(
            f"Could not read Excel file '{file_path.name}': {exc}"
        ) from exc
    return _clean_headers(frame)


def read_bank_statement_file(
    path: str | Path, *, max_header_scan_rows: int = 150
) -> BankStatementReadResult:
    """Read a bank statement and locate its transaction header automatically.

    Supports CSV, XLSX and XLS. CSV parsing is performed from the detected
    header row rather than from the beginning of the file, so preamble rows
    with different field counts do not break parsing. Original physical row
    numbers are retained as the DataFrame index for audit use.
    """
    file_path = _validate_file(path)
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        return _read_bank_csv(file_path, max_header_scan_rows=max_header_scan_rows)
    return _read_bank_excel(file_path, max_header_scan_rows=max_header_scan_rows)


def _read_bank_csv(path: Path, *, max_header_scan_rows: int) -> BankStatementReadResult:
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

        best: tuple[int, int, str, list[list[str]]] | None = None
        for delimiter in BANK_DELIMITERS:
            try:
                rows = list(
                    csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
                )
            except csv.Error:
                continue
            header = _find_header_row(rows, max_header_scan_rows)
            if header is None:
                continue
            score, header_index = header
            candidate = (score, header_index, delimiter, rows)
            if best is None or candidate[0] > best[0]:
                best = candidate

        if best is None:
            raise FileReadError(
                f"Could not locate a transaction-table header in '{path.name}'. "
                "Expected columns resembling date, amount, and narration or reference."
            )

        _, header_index, delimiter, rows = best
        headers = _unique_headers(rows[header_index])
        data: list[list[str]] = []
        row_numbers: list[int] = []
        extra_column_count = 0

        for physical_index, row in enumerate(
            rows[header_index + 1 :], start=header_index + 1
        ):
            if not row or all(not str(cell).strip() for cell in row):
                continue
            cleaned = [str(cell).strip() for cell in row]
            if len(cleaned) > len(headers):
                overflow = cleaned[len(headers) :]
                if any(value.strip() for value in overflow):
                    # Preserve unexpected extra fields instead of dropping data.
                    extra_column_count = max(extra_column_count, len(overflow))
            data.append(cleaned)
            row_numbers.append(physical_index + 1)

        if extra_column_count:
            headers.extend([f"__extra_{i + 1}" for i in range(extra_column_count)])
        width = len(headers)
        normalized_rows = [
            row[:width] + [""] * max(0, width - len(row)) for row in data
        ]
        frame = pd.DataFrame(normalized_rows, columns=headers, dtype=str)
        frame.index = row_numbers
        frame.index.name = "source_row_number"
        frame = _clean_headers(frame)
        metadata = _extract_metadata_from_text(text)
        metadata["header_row_number"] = header_index + 1
        metadata["delimiter"] = "TAB" if delimiter == "\t" else delimiter
        return BankStatementReadResult(
            frame=frame,
            source_file=path.name,
            header_row_number=header_index + 1,
            metadata=metadata,
            delimiter=delimiter,
        )

    raise FileReadError(f"Could not read CSV '{path.name}'.") from last_error


def _read_bank_excel(
    path: Path, *, max_header_scan_rows: int
) -> BankStatementReadResult:
    try:
        workbook = pd.ExcelFile(path)
    except ImportError as exc:
        detail = "Install xlrd for .xls files or openpyxl for .xlsx files."
        raise FileReadError(f"Excel support dependency is missing. {detail}") from exc
    except Exception as exc:
        raise FileReadError(f"Could not open Excel file '{path.name}': {exc}") from exc

    best: tuple[int, int, str, pd.DataFrame] | None = None
    all_metadata_values: list[str] = []
    for sheet in workbook.sheet_names:
        try:
            raw = pd.read_excel(
                path, sheet_name=sheet, header=None, dtype=str, keep_default_na=False
            )
        except Exception as exc:
            raise FileReadError(
                f"Could not read sheet '{sheet}' from '{path.name}': {exc}"
            ) from exc
        for _, row in raw.head(max_header_scan_rows).iterrows():
            all_metadata_values.extend(
                str(value) for value in row.tolist() if str(value).strip()
            )
        header_result = _find_header_frame(raw, max_header_scan_rows)
        if header_result is None:
            continue
        score, header_index = header_result
        if best is None or score > best[0]:
            best = (score, header_index, sheet, raw)

    if best is None:
        raise FileReadError(
            f"Could not locate a transaction-table header in '{path.name}'. "
            "Expected columns resembling date, amount, and narration or reference."
        )

    _, header_index, sheet_name, raw = best
    headers = _unique_headers([str(value) for value in raw.iloc[header_index].tolist()])
    body = raw.iloc[header_index + 1 :].copy()
    body.columns = headers
    body = body.fillna("").astype(str)

    # Keep non-empty physical rows, including summary/footer rows. The adapter
    # will later identify actual transactions based on the mapped date/amount.
    keep_mask = body.apply(lambda row: any(str(v).strip() for v in row), axis=1)
    body = body.loc[keep_mask]
    body.index = [
        int(index) + 1 for index in body.index
    ]  # Excel row numbers are 1-based.
    body.index.name = "source_row_number"
    body = _clean_headers(body)

    metadata = _extract_metadata_from_text("\n".join(all_metadata_values))
    metadata["header_row_number"] = header_index + 1
    metadata["sheet_name"] = sheet_name
    return BankStatementReadResult(
        frame=body,
        source_file=path.name,
        header_row_number=header_index + 1,
        metadata=metadata,
        sheet_name=sheet_name,
    )


def _find_header_row(rows: list[list[str]], scan_limit: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for index, row in enumerate(rows[:scan_limit]):
        score = _score_header([str(value) for value in row])
        if best is None or score > best[0]:
            best = (score, index)
    return best if best and best[0] >= 3 else None


def _find_header_frame(frame: pd.DataFrame, scan_limit: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for index in range(min(len(frame), scan_limit)):
        score = _score_header([str(value) for value in frame.iloc[index].tolist()])
        if best is None or score > best[0]:
            best = (score, index)
    return best if best and best[0] >= 3 else None


def _score_header(values: list[str]) -> int:
    normalized = {_normalize_header(value) for value in values if str(value).strip()}
    groups_found = sum(
        1
        for aliases in _HEADER_GROUPS.values()
        if any(alias in normalized for alias in aliases)
    )
    has_date = any(alias in normalized for alias in _HEADER_GROUPS["date"])
    has_amount = any(alias in normalized for alias in _HEADER_GROUPS["amount"])
    has_context = any(
        alias in normalized for alias in _HEADER_GROUPS["narration"]
    ) or any(alias in normalized for alias in _HEADER_GROUPS["reference"])
    # A transaction header must contain date + amount + narration/reference.
    return groups_found if has_date and has_amount and has_context else 0


def _normalize_header(value: str) -> str:
    text = str(value).replace("\ufeff", "").strip().casefold()
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _unique_headers(values: list[str]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for position, value in enumerate(values, start=1):
        header = str(value).replace("\ufeff", "").strip() or f"unnamed_{position}"
        count = counts.get(header, 0) + 1
        counts[header] = count
        headers.append(header if count == 1 else f"{header}__{count}")
    return headers


def _extract_metadata_from_text(text: str) -> dict[str, Any]:
    cleaned = text.replace("\ufeff", "")
    metadata: dict[str, Any] = {}

    account_patterns = (
        r"statement\s+of\s+account\s+no\s*[-:]?\s*([A-Z0-9]+)",
        r"account\s+no\.?\s*[:\-]?\s*([A-Z0-9]+)",
        r"account\s+no\.?[\"']?\s*,\s*[\"']?([A-Z0-9]+)",
        r"account\s+number\s*[:\-]?\s*([A-Z0-9]+)",
    )
    for pattern in account_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            metadata["account_number"] = match.group(1).strip()
            break

    name_patterns = (
        r"(?im)^\s*M/S\.\s*([^\r\n]+)",
        r"(?im)^\s*name\s*[:\-]+\s*([^\r\n]+)",
        r"(?im)^\s*customer\s+name\s*(?:\([^)]*\))?\s*[, :\-]?\s*([^\r\n]+)",
    )
    for pattern in name_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip(' :-,\t"')
            # Some bank exports repeat the account holder in parenthetical
            # legal-name fields. Keep the first display name, not the full
            # nested legal-name annotation.
            value = re.sub(r"\s*\(.*$", "", value).strip(' :-,\t"')
            if value and not value.casefold().startswith("account"):
                metadata["account_holder"] = value
                break

    if "account_holder" not in metadata:
        # Kotak-style exports may put the account holder alone on the second
        # preamble row without a Name/Customer label. Use only a conservative
        # fallback before the transaction header is encountered.
        for line in cleaned.splitlines()[:30]:
            candidate = line.strip().strip("\"' ")
            if not candidate:
                continue
            normalized = re.sub(r"[^a-z0-9]+", " ", candidate.casefold()).strip()
            if any(
                token in normalized
                for token in (
                    "account statement",
                    "statement of accounts",
                    "account no",
                    "period",
                    "currency",
                    "branch",
                    "ifsc",
                    "micr",
                    "address",
                    "phone",
                    "email",
                    "page no",
                    "customer id",
                    "cust reln",
                )
            ):
                continue
            # Skip key/value and address-like lines; accept a plain company
            # name line only. Avoid treating arbitrary transaction narration
            # as account-holder metadata.
            if (
                ":" not in candidate
                and len(candidate) >= 4
                and not re.search(r"\d{4,}", candidate)
            ):
                metadata["account_holder"] = candidate
                break

    period_patterns = (
        r"from\s*[:\-]?\s*([0-9]{1,2}[\-/][A-Z0-9]{2,4}[\-/][0-9]{2,4})\s*to\s*[:\-]?\s*([0-9]{1,2}[\-/][A-Z0-9]{2,4}[\-/][0-9]{2,4})",
        r"from\s+date\s*[, :]\s*([^\r\n,]+).*?to\s+date\s*[, :]\s*([^\r\n,]+)",
    )
    for pattern in period_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE | re.DOTALL)
        if match:
            metadata["period_from"] = match.group(1).strip()
            metadata["period_to"] = match.group(2).strip()
            break

    return metadata


def _clean_headers(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [
        str(column).replace("\ufeff", "").strip() for column in result.columns
    ]
    if not len(result.columns) or all(not str(c).strip() for c in result.columns):
        raise FileReadError("The input file does not contain usable column headers.")
    return result


def missing_columns(frame: pd.DataFrame, required: Iterable[str]) -> list[str]:
    """Return required column labels that are absent (case/space insensitive)."""
    actual = {str(column).strip().casefold() for column in frame.columns}
    return [column for column in required if column.strip().casefold() not in actual]
