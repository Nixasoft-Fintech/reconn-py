from __future__ import annotations

from fileinput import filename
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.base import AdapterError
from app.adapters.bank_statement_adapter import (
    BankColumnMapping,
    BankMappingError,
    BankStatementAdapter,
    normalize_bank_statement,
)
from app.adapters.fund_request_adapter import FundRequestAdapter
from app.adapters.va_transaction_adapter import VATransactionAdapter
from app.core.file_reader import (
    FileReadError,
    SUPPORTED_EXTENSIONS,
    read_tabular_file,
    read_bank_statement_file,
)
from app.core.inspection import inspect_report
from app.matching.engine import MatchingConfig, reconcile_transactions
from app.services.persistence_service import (
    save_reconciliation,
    build_frontend_summary,
)

APP_TITLE = "Bank Statement Reconciliation API"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

app = FastAPI(title=APP_TITLE, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_date": (
        "transaction date",
        "txn date",
        "date time",
        "date",
        "value date",
    ),
    "utr": ("utr", "utr number", "unique transaction reference", "rrn"),
    "reference_number": (
        "reference number",
        "ref no",
        "bank reference",
        "transaction reference",
    ),
    "narration": (
        "narration",
        "particulars",
        "description",
        "remarks",
        "transaction details",
    ),
    "debit_amount": ("debit amount", "debit", "withdrawal", "withdrawal amount"),
    "credit_amount": ("credit amount", "credit", "deposit", "deposit amount"),
    "amount": ("amount", "transaction amount"),
    "transaction_type": ("transaction type", "debit credit", "dr cr", "type"),
    "user": ("user", "customer name", "account holder"),
    "beneficiary_name": (
        "beneficiary name",
        "beneficiary",
        "payee name",
        "sender name",
    ),
    "account_number": ("account number", "account no", "a c no"),
    "virtual_account_number": ("va no", "virtual account number", "virtual account"),
}


def _safe_filename(filename: str | None) -> str:
    name = (filename or "uploaded_file").replace("\\", "/").split("/")[-1].strip()
    return name or "uploaded_file"


async def _save_upload(upload: UploadFile, folder: Path) -> Path:
    original_name = _safe_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type for '{original_name}'. Use CSV, XLSX or XLS.",
        )

    target = folder / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"'{original_name}' exceeds the 50 MB upload limit.",
                    )
                output.write(chunk)
    finally:
        await upload.close()
    if total == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"'{original_name}' is empty.")
    return target


def _json_sample(frame, limit: int = 5) -> list[dict[str, Any]]:
    sample = frame.head(limit).fillna("")
    return [
        {str(key): (None if value == "" else str(value)) for key, value in row.items()}
        for row in sample.to_dict(orient="records")
    ]


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _suggest_column_candidates(columns: list[str]) -> dict[str, list[str]]:
    normalized = {_normalized_header(column): column for column in columns}
    suggestions: dict[str, list[str]] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        matches = [normalized[alias] for alias in aliases if alias in normalized]
        suggestions[field_name] = matches
    return suggestions


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": APP_TITLE}


@app.post("/api/inspect-reports")
async def inspect_reports(
    fund_request_file: UploadFile = File(...),
    va_transaction_file: UploadFile = File(...),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bank-recon-inspect-") as temp_dir:
        folder = Path(temp_dir)
        try:
            fund_path = await _save_upload(fund_request_file, folder)
            va_path = await _save_upload(va_transaction_file, folder)
            fund_frame, fund_transactions = FundRequestAdapter().read(str(fund_path))
            va_frame, va_transactions = VATransactionAdapter().read(str(va_path))
        except (FileReadError, AdapterError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return {
            "fund_request": {
                "file_name": _safe_filename(fund_request_file.filename),
                "report_type": "fund_request",
                "inspection": inspect_report(fund_frame, fund_transactions),
            },
            "va_transaction": {
                "file_name": _safe_filename(va_transaction_file.filename),
                "report_type": "va_transaction",
                "inspection": inspect_report(va_frame, va_transactions),
            },
        }


@app.post("/api/inspect-bank-statement")
async def inspect_bank_statement(
    bank_statement_file: UploadFile = File(...),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bank-recon-bank-inspect-") as temp_dir:
        folder = Path(temp_dir)
        try:
            bank_path = await _save_upload(bank_statement_file, folder)
            frame = read_tabular_file(bank_path)
        except FileReadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        columns = [str(column) for column in frame.columns]
        return {
            "file_name": _safe_filename(bank_statement_file.filename),
            "record_count": int(len(frame)),
            "columns": columns,
            "sample_rows": _json_sample(frame),
            "possible_column_matches": _suggest_column_candidates(columns),
            "notice": (
                "These are column-name suggestions only. Confirm mappings and transaction "
                "direction against the real bank statement before reconciliation."
            ),
        }


@app.post("/api/reconcile")
async def reconcile(
    bank_statement_file: list[UploadFile] = File(...),
    fund_request_file: UploadFile = File(...),
    va_transaction_file: UploadFile = File(...),
    bank_mapping: str | None = Form(None),
) -> dict[str, Any]:
    """
    Reconcile Fund Request and VA Transaction reports against
    one or multiple bank statements.

    Automatic bank detection is used when bank_mapping is omitted.
    Manual mapping is retained for a single custom bank statement.
    """

    if not bank_statement_file:
        raise HTTPException(
            status_code=422,
            detail="Upload at least one bank statement.",
        )

    if len(bank_statement_file) > 10:
        raise HTTPException(
            status_code=422,
            detail="A maximum of 10 bank statements can be uploaded at once.",
        )

    # Manual mapping is supported for one custom statement only.
    mapping = None

    if bank_mapping and bank_mapping.strip():
        if len(bank_statement_file) != 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Manual bank_mapping supports only one bank statement. "
                    "For multiple statements, leave bank_mapping empty "
                    "and use automatic bank detection."
                ),
            )

        try:
            mapping_payload = json.loads(bank_mapping)

            if not isinstance(mapping_payload, dict):
                raise BankMappingError("bank_mapping must be a JSON object.")

            mapping = BankColumnMapping.from_dict(mapping_payload)

        except (
            json.JSONDecodeError,
            BankMappingError,
            TypeError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid bank mapping: {exc}",
            ) from exc

    with tempfile.TemporaryDirectory(prefix="bank-recon-run-") as temp_dir:

        folder = Path(temp_dir)

        try:
            # Read source reports.
            fund_path = await _save_upload(fund_request_file, folder)

            va_path = await _save_upload(va_transaction_file, folder)

            _, fund_transactions = FundRequestAdapter().read(str(fund_path))

            _, va_transactions = VATransactionAdapter().read(str(va_path))

            # Normalize every bank statement separately.
            bank_transactions = []
            bank_files_processed = []

            for upload in bank_statement_file:
                bank_path = await _save_upload(upload, folder)

                filename = _safe_filename(upload.filename)

                if mapping is not None:
                    # Manual mapping for one custom statement.
                    bank_frame = read_tabular_file(bank_path)

                    adapter = BankStatementAdapter(
                        mapping,
                        bank_name="Manual mapping",
                        bank_format="Manual",
                        source_filename=filename,
                    )

                    transactions = adapter.normalize(bank_frame)

                    profile_name = "Manual mapping"
                    bank_format = "Manual"
                    metadata = {}

                else:
                    # Automatic bank detection.
                    read_result = read_bank_statement_file(bank_path)

                    profile, transactions = normalize_bank_statement(read_result)

                    profile_name = profile.bank_name
                    bank_format = profile.bank_format
                    metadata = read_result.metadata

                # Always preserve the original uploaded filename.
                for transaction in transactions:
                    transaction.source_filename = filename

                # Keep transaction records from all uploaded files.
                bank_transactions.extend(transactions)

                bank_files_processed.append(
                    {
                        "file_name": filename,
                        "bank_name": profile_name,
                        "bank_format": bank_format,
                        "account_number": metadata.get("account_number"),
                        "account_holder": metadata.get("account_holder"),
                        "transaction_count": len(transactions),
                    }
                )

        except (
            FileReadError,
            AdapterError,
            BankMappingError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        # Run reconciliation once using all bank transactions.
        output = reconcile_transactions(
            [
                *fund_transactions,
                *va_transactions,
            ],
            bank_transactions,
            MatchingConfig(),
        )

        # Persist the complete reconciliation job.
        job_id = save_reconciliation(
            output,
            fund_request_filename=_safe_filename(fund_request_file.filename),
            va_transaction_filename=_safe_filename(va_transaction_file.filename),
            bank_files=bank_files_processed,
        )

        return {
            "job_id": str(job_id),
            "status": "COMPLETED",
            "summary": build_frontend_summary(output),
            "bank_files_processed": bank_files_processed,
            "notice": (
                "All uploaded bank statements were normalized separately "
                "and combined for reconciliation. Automatic bank detection "
                "was used unless manual mapping was supplied. Review "
                "unmatched transactions, duplicates and exceptions."
            ),
        }
