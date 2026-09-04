"""One-off: give a row its own SKU, then clear its OnBuy state so it is
created fresh under it.

Written for GTV rows 5613 and 5614, which both carried SKU 427291538928
and therefore both pointed at one listing (OPC PY6V6JG). Only one product
can own a SKU, so the other row has been selling under a listing that
shows someone else's product - the content scan reports it as a shift
that no repair can fix, because nothing is actually shifted.

Deliberately addressed BY ROW NUMBER, not by SKU: the SKU is duplicated,
so a SKU-addressed tool would match both rows and reset the innocent one
too. Row numbers move when the team inserts or deletes, so the row's
current SKU and the start of its title must both match what the caller
expects or nothing is written.

The new SKU is refused if any other row already uses it - that collision
is the whole reason this script exists. The Supabase mirror is left alone
on purpose: it is keyed by SKU, its entry for the old SKU belongs to the
row that legitimately keeps it, and the new SKU simply has no entry yet,
which is exactly what "create me fresh" looks like.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
ROW = int(os.getenv("ROW") or "0")
NEW_SKU = (os.getenv("NEW_SKU") or "").strip()
EXPECT_SKU = (os.getenv("EXPECT_SKU") or "").strip()
EXPECT_TITLE = (os.getenv("EXPECT_TITLE") or "").strip()
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")

CLEAR_COLS = ["Sync Status", "OPC", "Last OnBuy Sync", "OnBuy Product Created",
              "OnBuy Listing Active", "OnBuy Product ID", "Last Checked Time"]


def col_letter(n):
    s = ""
    while n >= 0:
        s = chr(n % 26 + 65) + s
        n = n // 26 - 1
    return s


def main():
    if not ROW or not NEW_SKU:
        raise SystemExit("ROW and NEW_SKU are both required")

    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"])
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    values = sheet.get_all_values()
    headers = [str(h).strip() for h in values[0]]
    col_map = {h: i for i, h in enumerate(headers)}
    if "SKU" not in col_map:
        raise SystemExit("no SKU column")

    if not 2 <= ROW <= len(values):
        raise SystemExit(f"row {ROW} is outside the sheet (rows 2..{len(values)})")
    row = values[ROW - 1]

    def cell(name):
        i = col_map.get(name)
        return (row[i] if i is not None and i < len(row) else "").strip()

    current_sku, title = cell("SKU"), cell("Title")
    print(f"row {ROW}: SKU {current_sku!r} | title {title[:70]!r}")

    if EXPECT_SKU and current_sku != EXPECT_SKU:
        raise SystemExit(f"ABORT: expected SKU {EXPECT_SKU!r}, found {current_sku!r} - rows have moved")
    if EXPECT_TITLE and not title.lower().startswith(EXPECT_TITLE.lower()):
        raise SystemExit(f"ABORT: expected title to start {EXPECT_TITLE!r}, found {title[:70]!r}")

    i_sku = col_map["SKU"]
    clashes = [r for r in range(2, len(values) + 1)
               if r != ROW and (values[r - 1][i_sku] if i_sku < len(values[r - 1]) else "").strip() == NEW_SKU]
    if clashes:
        raise SystemExit(f"ABORT: SKU {NEW_SKU} is already used by row(s) {clashes} - "
                         "assigning it would recreate the very collision this fixes")

    still_shared = [r for r in range(2, len(values) + 1)
                    if r != ROW and (values[r - 1][i_sku] if i_sku < len(values[r - 1]) else "").strip() == current_sku]
    print(f"rows keeping {current_sku!r} after this change: {still_shared or 'none'}")

    updates = [{"range": f"{col_letter(i_sku)}{ROW}", "values": [[NEW_SKU]]}]
    for name in CLEAR_COLS:
        if name in col_map:
            updates.append({"range": f"{col_letter(col_map[name])}{ROW}", "values": [[""]]})

    print(f"would set SKU -> {NEW_SKU} and clear {len(updates) - 1} OnBuy state column(s)")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return

    sheet.batch_update(updates, value_input_option="RAW")
    print(f"WROTE row {ROW}: SKU {current_sku} -> {NEW_SKU}, OnBuy state cleared")
    print("the next sync creates it fresh under its own SKU")


if __name__ == "__main__":
    main()
