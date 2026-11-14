from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FUND_CSV = (
    "ID,Date Time,User,Amount,Ref No,Status\n"
    "F1,10-11-2026 10:00:00,ACME,100.00,631418480656,approved\n"
)

VA_CSV = (
    "ID,Date Time,User,Txn ID,UTR,VA No,Amount,Status\n"
    "V1,10-11-2026 10:00:00,ACME,T1,631418480657,VA01,200.00,success\n"
)

BANK_CSV = (
    "UTR,Reference,Amount,Customer,Date\n"
    "631418480657,,200.00,ACME,10-11-2026 10:02:00\n"
    "631418480656,,100.00,ACME,10-11-2026 10:03:00\n"
)


def test_reconcile_endpoint_returns_fund_request_and_va_results():
    response = client.post(
        "/api/reconcile",
        files={
            "fund_request_file": ("fund.csv", FUND_CSV, "text/csv"),
            "va_transaction_file": ("va.csv", VA_CSV, "text/csv"),
            "bank_statement_file": ("bank.csv", BANK_CSV, "text/csv"),
        },
        data={
            "bank_mapping": (
                '{"utr":"UTR","reference_number":"Reference",'
                '"amount":"Amount","user":"Customer","transaction_date":"Date"}'
            )
        },
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["summary"]["source_record_count"] == 2
    assert payload["summary"]["matched_count"] == 2
    assert len(payload["results"]) == 2

    results_by_type = {result["source_type"]: result for result in payload["results"]}

    assert results_by_type["fund_request"]["final_status"] == "MATCHED"
    assert results_by_type["va_transaction"]["final_status"] == "MATCHED"


def test_reconcile_endpoint_requires_valid_bank_mapping():
    response = client.post(
        "/api/reconcile",
        files={
            "fund_request_file": ("fund.csv", FUND_CSV, "text/csv"),
            "va_transaction_file": ("va.csv", VA_CSV, "text/csv"),
            "bank_statement_file": ("bank.csv", BANK_CSV, "text/csv"),
        },
        data={"bank_mapping": '{"narration":"Narration"}'},
    )

    assert response.status_code == 422
    assert "reference field" in response.json()["detail"]
