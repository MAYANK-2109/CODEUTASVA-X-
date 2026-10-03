import io
import openpyxl
from app.portfolio.extractor import extract_holdings_from_excel, resolve_symbol


def test_resolve_symbol():
    assert resolve_symbol("Reliance Industries Ltd") == "RELIANCE"
    assert resolve_symbol("Tata Consultancy Services") == "TCS"
    assert resolve_symbol("Infosys Limited") == "INFY"
    assert resolve_symbol("HDFC Bank") == "HDFCBANK"
    assert resolve_symbol("Tata Motors") == "TATAMOTORS"
    assert resolve_symbol("Random Stock (ABCDEF)") == "ABCDEF"


def test_excel_with_top_metadata_and_ltp():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Portfolio"
    ws.append(["Client Name: John Doe", "Client Code: 998877"])
    ws.append(["Generated on: 2026-10-03"])
    ws.append([])
    ws.append(["Symbol", "Company Name", "Qty", "Avg Buy Price", "LTP", "Current Value"])
    ws.append(["INFY", "Infosys Ltd", 15, 1420.5, 1510.0, 22650.0])
    ws.append(["TCS", "Tata Consultancy Services", 8, 3500.0, 3750.0, 30000.0])
    ws.append(["Total", "", 23, "", "", 52650.0])

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    holdings = extract_holdings_from_excel(data, "xlsx")
    assert len(holdings) == 2

    h1 = holdings[0]
    assert h1["symbol"] == "INFY"
    assert h1["name"] == "Infosys Ltd"
    assert h1["units"] == 15.0
    assert h1["buy_price"] == 1420.5
    assert h1["current_price"] == 1510.0

    h2 = holdings[1]
    assert h2["symbol"] == "TCS"
    assert h2["name"] == "Tata Consultancy Services"
    assert h2["units"] == 8.0
    assert h2["buy_price"] == 3500.0
    assert h2["current_price"] == 3750.0


def test_excel_without_symbol_column():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Particulars", "Holding Quantity", "Purchase Price"])
    ws.append(["Reliance Industries Limited", 25, 2350.0])
    ws.append(["HDFC Bank Ltd", 40, 1480.0])

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    holdings = extract_holdings_from_excel(data, "xlsx")
    assert len(holdings) == 2

    assert holdings[0]["symbol"] == "RELIANCE"
    assert holdings[0]["units"] == 25.0
    assert holdings[0]["buy_price"] == 2350.0

    assert holdings[1]["symbol"] == "HDFCBANK"
    assert holdings[1]["units"] == 40.0
    assert holdings[1]["buy_price"] == 1480.0
