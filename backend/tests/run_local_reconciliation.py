from __future__ import annotations

import sys
import json
import mimetypes
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient

from app.main import app
from app.core.file_reader import read_bank_statement_file
from app.adapters.bank_statement_adapter import normalize_bank_statement

UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"


def find_one(pattern: str, description: str) -> Path:
    matches = list(UPLOAD_DIR.glob(pattern))

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {description}, found {len(matches)}: "
            f"{[p.name for p in matches]}"
        )

    return matches[0]


def main() -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    fund_file = find_one(
        "Fund_Request_Report*.csv",
        "Fund Request report",
    )

    va_file = find_one(
        "VA Transaction Report*.csv",
        "VA Transaction report",
    )

    source_files = {
        fund_file.resolve(),
        va_file.resolve(),
    }

    bank_files = sorted(
        path
        for path in UPLOAD_DIR.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".csv", ".xlsx", ".xls"}
        and path.resolve() not in source_files
    )

    if not bank_files:
        raise RuntimeError("No bank statements found")

    print("\nINPUT FILES")
    print("-" * 60)
    print("Fund Request:", fund_file.name)
    print("VA Transaction:", va_file.name)

    for index, path in enumerate(bank_files, start=1):
        print(f"Bank statement {index}: {path.name}")

    # --------------------------------------------------
    # Step 1: Check each bank statement independently.
    # --------------------------------------------------

    print("\nCHECKING BANK STATEMENTS INDIVIDUALLY")
    print("-" * 60)

    for path in bank_files:
        print(f"\nChecking: {path.name}")

        read_result = None

        try:
            # Read the current bank statement
            read_result = read_bank_statement_file(path)

            # Normalize the statement
            profile, transactions = normalize_bank_statement(read_result)

            print(f"Bank: {profile.bank_name}")
            print(f"Format: {profile.bank_format}")
            print(f"Transactions: {len(transactions)}")
            print("Status: PASSED")

        except Exception as exc:
            print("Status: FAILED")
            print(f"Error: {exc}")

            if path.suffix.lower() == ".xls" and read_result is not None:
                frame = read_result.frame

                print("\nHDFC DIAGNOSTICS")
                print("-" * 60)

                print("Read result type:", type(read_result))
                print("Frame type:", type(frame))
                print("Frame shape:", frame.shape)
                print("Frame columns:", frame.columns.tolist())
                print("Frame index:", frame.index.tolist())

                print("\nHDFC DATAFRAME CONTENT:")
                print(frame.to_string(index=True))

                print("\nREAD RESULT DETAILS:")
                print(read_result)

            return

    # --------------------------------------------------
    # Step 2: Submit all six files to the API.
    # --------------------------------------------------

    print("\nSubmitting reconciliation request...")

    with ExitStack() as stack:
        files = [
            (
                "fund_request_file",
                (
                    fund_file.name,
                    stack.enter_context(fund_file.open("rb")),
                    mimetypes.guess_type(fund_file.name)[0]
                    or "application/octet-stream",
                ),
            ),
            (
                "va_transaction_file",
                (
                    va_file.name,
                    stack.enter_context(va_file.open("rb")),
                    mimetypes.guess_type(va_file.name)[0] or "application/octet-stream",
                ),
            ),
        ]

        for path in bank_files:
            files.append(
                (
                    "bank_statement_file",
                    (
                        path.name,
                        stack.enter_context(path.open("rb")),
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    ),
                )
            )

        with TestClient(app) as client:
            response = client.post(
                "/api/reconcile",
                files=files,
            )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = RESULT_DIR / f"reconciliation_{timestamp}.json"

    try:
        response_data = response.json()
    except ValueError:
        response_data = {"response_text": response.text}

    result_path.write_text(
        json.dumps(
            {
                "http_status": response.status_code,
                "success": response.is_success,
                "input_files": {
                    "fund_request": fund_file.name,
                    "va_transaction": va_file.name,
                    "bank_statements": [path.name for path in bank_files],
                },
                "response": response_data,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\nAPI RESULT")
    print("-" * 60)
    print("HTTP status:", response.status_code)
    print("Saved result:", result_path)

    if not response.is_success:
        print("\nRECONCILIATION FAILED")
        print(
            json.dumps(
                response_data,
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print("\nPROCESSED BANK STATEMENTS")

    processed = response_data.get(
        "bank_files_processed",
        [],
    )

    for item in processed:
        print(
            f"- {item.get('file_name')}: "
            f"{item.get('bank_name')} | "
            f"{item.get('bank_format')} | "
            f"{item.get('transaction_count')} transactions"
        )

    print("\nRESPONSE KEYS:")
    print(list(response_data.keys()))

    if "summary" in response_data:
        print("\nRECONCILIATION SUMMARY:")
        print(
            json.dumps(
                response_data["summary"],
                indent=2,
            )
        )

    print("\nFull response saved in results folder.")


if __name__ == "__main__":
    main()
