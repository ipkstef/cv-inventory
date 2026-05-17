"""Generate TCGplayer seller-template CSV from confirmed-row dicts."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from cv_inventory.tcgplayer.store import TCGStore

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


def _fmt_money(v) -> str:
    return "" if v is None else f"{v:.2f}"


def build_seller_csv(store: TCGStore, rows: Iterable[dict]) -> bytes:
    """Render the seller-template CSV.

    Each input row: {"product_id", "printing", "condition", "language", "quantity",
                     optional "marketplace_price"}.
    Unresolvable rows are emitted with blank TCGplayer Id so the caller can spot them.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SELLER_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for r in rows:
        product = store.product(int(r["product_id"]))
        if product is None:
            writer.writerow({c: "" for c in SELLER_COLUMNS})
            continue
        sku = store.resolve_sku(
            int(r["product_id"]),
            printing=r["printing"],
            condition=r["condition"],
            language=r["language"],
        )
        writer.writerow(
            {
                "TCGplayer Id": "" if sku is None else str(sku["sku_id"]),
                "Product Line": "Magic",
                "Set Name": product["set_name"] or "",
                "Product Name": product["name"] or "",
                "Title": "",
                "Number": product["collector_number"] or "",
                "Rarity": product["rarity"] or "",
                "Condition": r["condition"],
                "TCG Market Price": _fmt_money(sku["market_price"]) if sku else "",
                "TCG Direct Low": _fmt_money(sku["direct_low_price"]) if sku else "",
                "TCG Low Price With Shipping": "",
                "TCG Low Price": _fmt_money(sku["low_price"]) if sku else "",
                "Total Quantity": str(int(r["quantity"])),
                "Add to Quantity": "",
                "TCG Marketplace Price": _fmt_money(r.get("marketplace_price")),
                "Photo URL": product["image_url"] or "",
            }
        )

    return buf.getvalue().encode("utf-8")
