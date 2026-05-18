"""Generate TCGplayer seller-template CSV from confirmed-row dicts."""

from __future__ import annotations

import csv
import io
from collections import OrderedDict
from collections.abc import Iterable

from cv_inventory.tcgplayer.store import TCGStore


class MergePriceConflict(ValueError):
    """Raised when merge_duplicates encounters different marketplace_price for the same SKU.

    Attribute ``conflicts`` is a list of dicts:
        [{"sku_key": {...}, "prices_seen": [1.49, 1.99]}, ...]
    Routes translate this to an HTTP 400 with a structured error body.
    """

    def __init__(self, conflicts: list[dict]) -> None:
        super().__init__(f"merge_duplicates found {len(conflicts)} price conflict(s)")
        self.conflicts = conflicts


def merge_duplicate_rows(rows: Iterable[dict]) -> list[dict]:
    """Sum quantities for rows sharing (product_id, printing, condition, language).

    If marketplace_price differs across rows in a group, raise MergePriceConflict.
    A None marketplace_price merges cleanly with any explicit price (the explicit
    one wins; nothing is "different" about an absent value).
    """
    merged: OrderedDict[tuple, dict] = OrderedDict()
    conflicts_by_key: dict[tuple, set] = {}

    for r in rows:
        key = (
            int(r["product_id"]),
            r["printing"],
            r["condition"],
            r["language"],
        )
        new_qty = int(r["quantity"])
        new_price = r.get("marketplace_price")
        if key not in merged:
            merged[key] = {**r, "quantity": new_qty, "marketplace_price": new_price}
            continue
        # Aggregate quantity.
        merged[key]["quantity"] += new_qty
        # Reconcile marketplace_price.
        existing_price = merged[key].get("marketplace_price")
        if new_price is not None and existing_price is not None and new_price != existing_price:
            conflicts_by_key.setdefault(key, {existing_price}).add(new_price)
        elif new_price is not None and existing_price is None:
            merged[key]["marketplace_price"] = new_price

    if conflicts_by_key:
        conflicts = [
            {
                "sku_key": {
                    "product_id": k[0],
                    "printing": k[1],
                    "condition": k[2],
                    "language": k[3],
                },
                "prices_seen": sorted(prices),
            }
            for k, prices in conflicts_by_key.items()
        ]
        raise MergePriceConflict(conflicts)

    return list(merged.values())


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


def build_seller_csv(
    store: TCGStore,
    rows: Iterable[dict],
    *,
    merge_duplicates: bool = True,
) -> bytes:
    """Render the seller-template CSV.

    Each input row: {"product_id", "printing", "condition", "language", "quantity",
                     optional "marketplace_price"}.
    Unresolvable rows are emitted with blank TCGplayer Id so the caller can spot them.

    ``merge_duplicates`` (default True): rows sharing
    (product_id, printing, condition, language) are summed into one row. If the
    rows have conflicting marketplace_price values, raises MergePriceConflict.
    """
    row_list = list(rows)
    if merge_duplicates:
        row_list = merge_duplicate_rows(row_list)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SELLER_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for r in row_list:
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
