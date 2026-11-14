import csv
from pathlib import Path

import pandas as pd
import pytest

from app.core.file_reader import FileReadError, read_bank_statement_file


def test_reads_csv_with_axis_preamble_and_finds_header(tmp_path: Path):
    path = tmp_path / "axis.csv"
    path.write_text(
        "Name :- ACME PVT LTD\n"
        "Statement of Account No - 123456789 for the period (From : 10/11/2026 To : 11/11/2026)\n"
        "S.NO,Transaction Date (dd/mm/yyyy),Value Date (dd/mm/yyyy),Particulars,Amount(INR),Debit/Credit,Balance(INR),Cheque Number,Branch Name(SOL)\n"
        "1,11/11/2026,11/11/2026,UPI/123456789012/ACME,1,000.00,CR,10,000.00,,BRANCH\n",
        encoding="utf-8",
    )
    result = read_bank_statement_file(path)
    assert result.header_row_number == 3
    assert result.metadata["account_number"] == "123456789"
    assert result.metadata["account_holder"] == "ACME PVT LTD"
    assert result.frame.iloc[0]["Particulars"] == "UPI/123456789012/ACME"
    assert result.frame.index.tolist() == [4]


def test_reads_kotak_csv_with_duplicate_dr_cr_headers(tmp_path: Path):
    path = tmp_path / "kotak.csv"
    path.write_text(
        '"","","Account Statement"\n'
        '"ACME PVT LTD"\n'
        '"","","","","Account No.","3550826229"\n'
        '"Sl. No.","Transaction Date","Value Date","Description","Chq /Ref No.","Amount","Dr / Cr","Balance","Dr / Cr"\n'
        '"1","10-11-2026 12:11:18","10-11-2026","UPI/123456789012","REF1","100.00","CR","200.00","CR"\n',
        encoding="utf-8",
    )
    result = read_bank_statement_file(path)
    assert result.header_row_number == 4
    assert result.metadata["account_number"] == "3550826229"
    assert result.frame.columns[-1] == "Dr / Cr__2"
    assert result.frame.index.tolist() == [5]


def test_reads_xlsx_with_header_after_metadata(tmp_path: Path):
    path = tmp_path / "custom.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([
            ["Account Statement", ""],
            ["Account No :123456", ""],
            ["Bank Reference", "Transaction Date & Time", "Type", "Payment Narration", "Debit", "Credit"],
            ["REF1", "10-NOV-26 10:00:00", "Credit", "IMPS/P2A/123456789012/BANK", "0", "100"],
        ]).to_excel(writer, index=False, header=False)
    result = read_bank_statement_file(path)
    assert result.header_row_number == 3
    assert result.metadata["account_number"] == "123456"
    assert result.sheet_name == "Sheet1"
    assert result.frame.iloc[0]["Payment Narration"] == "IMPS/P2A/123456789012/BANK"
    assert result.frame.index.tolist() == [4]


def test_rejects_csv_without_recognizable_transaction_header(tmp_path: Path):
    path = tmp_path / "unknown.csv"
    path.write_text("hello,world\none,two\n", encoding="utf-8")
    with pytest.raises(FileReadError, match="Could not locate"):
        read_bank_statement_file(path)


def test_reads_hdfc_style_xlsx_and_cleans_account_holder_metadata(tmp_path: Path):
    path = tmp_path / "hdfc.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([
            ["HDFC BANK Ltd.", "Statement of accounts"],
            ["M/S. NEXASOFT TECHNOLOGIES PVT LTD(NEXASOFT TECHNOLOGIES PVT LTD (LEGAL NAME))", ""],
            ["Account No :50200108946770", ""],
            ["Statement From : 01/11/2026 To : 10/11/2026", ""],
            ["********", ""],
            ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["********", "********", "********", "********", "********", "********", "********"],
            ["03/11/26", "IMPS PAYMENT", "0000630813465525", "03/11/26", "4000", "", "10000"],
        ]).to_excel(writer, index=False, header=False)

    result = read_bank_statement_file(path)

    assert result.header_row_number == 6
    assert result.metadata["account_number"] == "50200108946770"
    assert result.metadata["account_holder"] == "NEXASOFT TECHNOLOGIES PVT LTD"
    assert result.metadata["period_from"] == "01/11/2026"
    assert result.metadata["period_to"] == "10/11/2026"
    assert result.frame.columns.tolist() == [
        "Date", "Narration", "Chq./Ref.No.", "Value Dt",
        "Withdrawal Amt.", "Deposit Amt.", "Closing Balance",
    ]
    assert result.frame.index.tolist() == [7, 8]
