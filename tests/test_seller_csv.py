import csv
import io

from scan_and_identify.tcgplayer.seller_csv import build_seller_csv
from scan_and_identify.tcgplayer.store import TCGStore

SELLER_COLUMNS = [
    "TCGplayer Id",
    "Product Line",
    "Set Name",
    "Product Name",
    "Title",
    "Number",
    "Rarity",
    "Condition",
    "TCG Market Price",
    "TCG Direct Low",
    "TCG Low Price With Shipping",
    "TCG Low Price",
    "Total Quantity",
    "Add to Quantity",
    "TCG Marketplace Price",
    "Photo URL",
]


def test_csv_header_matches_tcgplayer_template(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    csv_bytes = build_seller_csv(store, rows=[])
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
    header = next(reader)
    assert header == SELLER_COLUMNS


def test_csv_resolves_sku_and_fills_columns(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    csv_bytes = build_seller_csv(
        store,
        rows=[
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 2,
                "marketplace_price": 1.49,
            },
        ],
    )
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    rows = list(reader)
    assert len(rows) == 1
    r = rows[0]
    assert r["TCGplayer Id"]
    assert r["Product Line"] == "Magic"
    assert r["Set Name"] == "Test Set Alpha"
    assert r["Product Name"] == "Alpha Card 1"
    assert r["Number"] == "1"
    assert r["Rarity"] == "Common"
    assert r["Condition"] == "Near Mint"
    assert r["Total Quantity"] == "2"
    assert r["TCG Marketplace Price"] == "1.49"
    assert "tcgplayer-cdn.tcgplayer.com" in r["Photo URL"]


def test_csv_unresolvable_row_blanks_sku(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    csv_bytes = build_seller_csv(
        store,
        rows=[
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Damaged",
                "language": "English",
                "quantity": 1,
            },
        ],
    )
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    rows = list(reader)
    assert rows[0]["TCGplayer Id"] == ""
