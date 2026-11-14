import pandas as pd

from app.adapters.fund_request_adapter import FundRequestAdapter
from app.adapters.va_transaction_adapter import VATransactionAdapter


def test_fund_request_adapter_normalizes_and_preserves_raw_data():
    frame = pd.DataFrame([{
        "ID": "1001", "Date Time": "10-11-2026 10:00:00", "User": "Acme Pvt Ltd",
        "Amount": "1,250.50", "Ref No": " ab 123 ", "Status": "Approved",
        "Bank": "AXIS BANK", "Mode": "RTGS"
    }])
    item = FundRequestAdapter().normalize(frame)[0]
    assert item.source_transaction_id == "1001"
    assert item.reference_number == "AB123"
    assert item.utr is None
    assert str(item.amount) == "1250.50"
    assert item.status == "approved"
    assert item.raw_data["Ref No"] == " ab 123 "


def test_va_adapter_uses_utr_as_reference():
    frame = pd.DataFrame([{
        "ID": "1001", "Date Time": "10-11-2026 10:00:00", "User": "Acme Pvt Ltd",
        "Txn ID": "TXN-1", "UTR": " 0012345 ", "VA No": "VA001",
        "Amount": "500.00", "Status": "SUCCESS"
    }])
    item = VATransactionAdapter().normalize(frame)[0]
    assert item.utr == "0012345"
    assert item.reference_number == "0012345"
    assert item.virtual_account_number == "VA001"
    assert item.status == "success"
