import csv
import io

from fastapi.testclient import TestClient

from scan_and_identify.server.app import create_app
from scan_and_identify.server.state import AppState


def test_export_returns_csv_with_correct_headers(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    r = client.post(
        "/export/tcgplayer-csv",
        json={
            "rows": [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                    "marketplace_price": 0.99,
                },
                {
                    "product_id": 1002,
                    "printing": "Foil",
                    "condition": "Lightly Played",
                    "language": "English",
                    "quantity": 4,
                },
            ]
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["Product Name"] == "Alpha Card 1"
    assert rows[0]["TCG Marketplace Price"] == "0.99"
    assert rows[1]["Total Quantity"] == "4"
    assert rows[1]["TCG Marketplace Price"] == ""
