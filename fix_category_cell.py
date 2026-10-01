"""ONE-OFF, narrowly scoped: corrects a single row's Category cell on a
named tab, after confirming that row still holds the expected SKU (abort
rather than write blindly if the sheet has shifted since the row number
was determined). Built 2026-10-01 for the persistent GTV Amazon-tab
rejection on SKU 443659452071 (row 3032, Category cell held the bare word
"Caps" - not a valid OnBuy category path - so every run since has logged
"OnBuy push failed ... Category cell not recognised by OnBuy").

Env: SHEET_TAB, TARGET_ROW, EXPECTED_SKU, NEW_CATEGORY.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = "OnBuy_Feed_Master"
SHEET_TAB = os.environ["SHEET_TAB"]
TARGET_ROW = int(os.environ["TARGET_ROW"])
EXPECTED_SKU = os.environ["EXPECTED_SKU"].strip()
NEW_CATEGORY = os.environ["NEW_CATEGORY"]


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    book = client.open(SHEET_NAME)
    tab = book.worksheet(SHEET_TAB)

    headers = [str(h).strip() for h in tab.row_values(1)]
    col_map = {h: i + 1 for i, h in enumerate(headers) if h}
    for required in ("SKU", "Category"):
        if required not in col_map:
            raise SystemExit(f"ABORT: tab {SHEET_TAB!r} has no {required!r} column")

    row_values = tab.row_values(TARGET_ROW)

    def cell(col):
        idx = col_map[col] - 1
        return row_values[idx].strip() if idx < len(row_values) else ""

    actual_sku = cell("SKU")
    if actual_sku != EXPECTED_SKU:
        raise SystemExit(f"ABORT: row {TARGET_ROW} holds SKU {actual_sku!r}, "
                          f"expected {EXPECTED_SKU!r} - sheet has shifted, not writing")

    old_category = cell("Category")
    print(f"row {TARGET_ROW} confirmed SKU {actual_sku} - Category {old_category!r} -> {NEW_CATEGORY!r}")

    from gspread.utils import rowcol_to_a1
    a1 = rowcol_to_a1(TARGET_ROW, col_map["Category"])
    tab.update(a1, [[NEW_CATEGORY]])
    print(f"wrote {a1} = {NEW_CATEGORY!r}")


if __name__ == "__main__":
    main()
