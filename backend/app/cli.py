from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.adapters.fund_request_adapter import FundRequestAdapter
from app.adapters.va_transaction_adapter import VATransactionAdapter
from app.core.inspection import inspect_report


def inspect_one(label: str, file_path: str, adapter: Any) -> dict[str, Any]:
    frame, transactions = adapter.read(file_path)
    return {
        "report_type": label,
        "file_name": Path(file_path).name,
        "inspection": inspect_report(frame, transactions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect source reconciliation reports."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect-reports", help="Inspect Fund Request and VA reports"
    )
    inspect_parser.add_argument(
        "--fund", required=True, help="Fund Request CSV/XLSX path"
    )
    inspect_parser.add_argument(
        "--va", required=True, help="VA Transaction CSV/XLSX path"
    )

    args = parser.parse_args()
    if args.command == "inspect-reports":
        result = {
            "fund_request": inspect_one(
                "fund_request", args.fund, FundRequestAdapter()
            ),
            "va_transaction": inspect_one(
                "va_transaction", args.va, VATransactionAdapter()
            ),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
