from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FUND_CSV = b"ID,Date Time,User,Amount,Ref No,Status,Bank,Mode\n1,10-11-2026 10:00:00,ACME PVT LTD,1000.00,REF123,approved,AXIS,RTGS\n"
VA_CSV = b"ID,Date Time,User,Txn ID,UTR,VA No,Amount,Status\n2,10-11-2026 10:10:00,ACME PVT LTD,TXN2,UTR123,VA001,500.00,success\n"


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_inspect_reports_endpoint():
    response = client.post(
        "/api/inspect-reports",
        files={
            "fund_request_file": ("fund.csv", FUND_CSV, "text/csv"),
            "va_transaction_file": ("va.csv", VA_CSV, "text/csv"),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fund_request"]["inspection"]["record_count"] == 1
    assert data["va_transaction"]["inspection"]["record_count"] == 1


def test_inspect_bank_statement_returns_column_suggestions_only():
    bank_csv = b"Transaction Date,UTR,Particulars,Debit,Credit,Amount\n10-11-2026,UTR123,ACME,1000,,1000\n"
    response = client.post(
        "/api/inspect-bank-statement",
        files={"bank_statement_file": ("bank.csv", bank_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["record_count"] == 1
    assert data["possible_column_matches"]["utr"] == ["UTR"]
    assert data["possible_column_matches"]["narration"] == ["Particulars"]
    assert "suggestions only" in data["notice"]
