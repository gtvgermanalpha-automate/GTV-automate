"""One-off: move existing OnBuy products to a different category.

The category is baked into a product at creation, so a wrong shelf (the
ZZA monitor created 2026-09-09 under "Computer Monitor Mounts & Stands"
because its description mentions the stand) is corrected on OnBuy with the
products batch update (PUT /v2/products, keyed by OPC) - the same call
repair_descriptions.py uses for descriptions. The sheet's Category cell is
NOT touched here (set_row_cell.py does that, and the sync never overwrites
a valid category), so run both.

SKUS: comma-separated. CATEGORY: the OnBuy category path exactly as in
onbuy_categories_only.csv (validated; the numeric ID is looked up here).
The OPC comes from Supabase, then the sheet tabs (SHEET_TABS, default
"Amazon", plus the first tab), then the live listings pager
(USE_LISTINGS_OPC=1) for rows whose OPC never landed. DRY_RUN=1 (default)
reports only.
"""
import csv
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import supabase_db
from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import raise_for_status, with_retry

SHEET_NAME = "OnBuy_Feed_Master"
SKUS = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]
CATEGORY = (os.getenv("CATEGORY") or "").strip()
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
USE_LISTINGS_OPC = (os.getenv("USE_LISTINGS_OPC") or "").strip().lower() in ("1", "yes", "true")
TABS = [t.strip() for t in (os.getenv("SHEET_TABS") or "Amazon").split(",") if t.strip()]


def category_id_for(path):
    with open("onbuy_categories_only.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("OnBuy Category Path") or "").strip().lower() == path.lower():
                return int(row["Category ID"])
    return None


def sheet_opcs(skus):
    """SKU -> OPC from the sheet tabs, for SKUs whose mirror row has none."""
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = gspread.authorize(creds).open(SHEET_NAME)
    titles = [w.title for w in book.worksheets()]
    sheets = [book.sheet1] + [book.worksheet(t) for t in TABS if t in titles and t != book.sheet1.title]
    out = {}
    for ws in sheets:
        headers = [str(h).strip() for h in ws.row_values(1)]
        if "SKU" not in headers or "OPC" not in headers:
            continue
        sku_col = ws.col_values(headers.index("SKU") + 1)
        opc_col = ws.col_values(headers.index("OPC") + 1)
        for i in range(1, len(sku_col)):
            sku = str(sku_col[i]).replace(",", "").strip()
            opc = str(opc_col[i]).strip() if i < len(opc_col) else ""
            if sku in skus and opc and opc.upper() != "PENDING":
                out.setdefault(sku, opc)
    return out


def main():
    if not SKUS or not CATEGORY:
        raise SystemExit("SKUS and CATEGORY are required")
    cat_id = category_id_for(CATEGORY)
    if cat_id is None:
        raise SystemExit(f"category is not a listable OnBuy path in onbuy_categories_only.csv: {CATEGORY!r}")
    print(f"category {CATEGORY!r} = id {cat_id}")

    opcs = {}
    try:
        for sku, fields in supabase_db.fetch_existing_fields(SKUS).items():
            opc = str((fields or {}).get("OPC") or "").strip()
            if opc and opc.upper() != "PENDING":
                opcs[sku] = opc
    except Exception as exc:  # noqa: BLE001 - the sheet is the fallback
        print(f"Supabase lookup failed ({str(exc)[:120]}) - falling back to the sheet")
    missing = [s for s in SKUS if s not in opcs]
    if missing:
        opcs.update(sheet_opcs(set(missing)))
        missing = [s for s in SKUS if s not in opcs]

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy authentication failed")
    if missing and USE_LISTINGS_OPC:
        from repair_descriptions import listings_opc_map
        live = listings_opc_map(onbuy)
        for sku in missing:
            if live.get(sku):
                opcs[sku] = live[sku]
        missing = [s for s in SKUS if s not in opcs]
    for sku in missing:
        print(f"{sku}: no OPC known yet (still in OnBuy's queue?) - skipped")
    for sku in SKUS:
        if sku in opcs:
            print(f"{sku}: OPC {opcs[sku]} -> category {cat_id}")

    products = [{"opc": opcs[s], "category_id": cat_id} for s in SKUS if s in opcs]
    if not products:
        return
    if DRY_RUN:
        print("DRY RUN - nothing pushed")
        return

    def _do():
        resp = onbuy._send("PUT", f"{BASE_URL}/products", what="products category update",
                           json={"site_id": onbuy.site_id, "seller_id": onbuy.seller_id,
                                 "products": products}, timeout=60)
        raise_for_status(resp, what="products category update")
        return resp.json()

    body = with_retry(_do, what="products category update", max_attempts=3)
    print("OnBuy response:", json.dumps(body)[:1500])


if __name__ == "__main__":
    main()
