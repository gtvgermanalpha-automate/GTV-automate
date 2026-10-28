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

2026-10-28 (GTV Amazon row 5198, SKU 693236535554 showed another product's
content): SHEET_TAB (workflow input `tab`) names the worksheet the row is on
(blank = the main product tab, as before; Amazon = the Amazon tab), and the
clash check now covers BOTH product tabs and also refuses a SKU whose digits
are another row's (the digits are the product code OnBuy gets). The run
prints whether the new SKU's digits are a valid barcode: a valid one is sent
to OnBuy as the product code and the listing ATTACHES to any product that
already carries it - even one another seller or another of our stores made
(the code that was typed here first, 761609482085, is YRA's dishwasher
product) - while a SKU without a valid barcode (e.g. Ama-<ASIN>-1) sends no
code, so OnBuy creates a new product.
"""
import json
import os
import re

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
SHEET_TAB = (os.getenv("SHEET_TAB") or "").strip()
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


def digits(sku):
    """The digits of a SKU: what the sync sends OnBuy as the product code when they form a valid barcode."""
    return re.sub(r"\D", "", str(sku or ""))


def valid_gtin(code):
    """The sync's own test (generate_xml.is_valid_gtin): 8 / 12 / 13 / 14 digits with a correct GS1 check digit, not a 5-prefixed coupon code."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    if (len(code) == 12 and code[0] == "5") or (len(code) == 13 and code.startswith("05")):
        return False
    body, check = code[:-1], code[-1]
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10) == check


def code_note(sku):
    """One plain sentence on what OnBuy will get as the product code for this SKU."""
    d = digits(sku)
    if valid_gtin(d):
        return (f"its digits {d} are a VALID barcode: OnBuy gets them as the product code and the listing attaches to any product that already carries that code "
                "(made by another seller or by another of our stores) - be sure the code is unused")
    return f"its digits {d or '(none)'} are not a valid barcode: OnBuy gets no product code, so the product is created new"


def clashes(tabs, new_sku, tab, row):
    """[(tab title, row number)] of every OTHER row of any tab whose SKU is `new_sku` or whose SKU digits (8 or more) are the same product code.
    `tabs` = {title: all values of the worksheet}; the row being changed (`tab`, `row`) is left out."""
    want = digits(new_sku)
    found = []
    for title, values in tabs.items():
        if not values:
            continue
        header = [str(h).strip() for h in values[0]]
        if "SKU" not in header:
            continue
        i = header.index("SKU")
        for n in range(2, len(values) + 1):
            if title == tab and n == row:
                continue
            other = (values[n - 1][i] if i < len(values[n - 1]) else "").strip()
            if other and (other == new_sku or (len(want) >= 8 and digits(other) == want)):
                found.append((title, n))
    return found


def main():
    if not ROW or not NEW_SKU:
        raise SystemExit("ROW and NEW_SKU are both required")

    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"])
    book = gspread.authorize(creds).open(SHEET_NAME)
    first = sheet_tabs.product_sheet(book)
    sheet = book.worksheet(SHEET_TAB) if SHEET_TAB else first
    tabs = {sheet.title: sheet.get_all_values()}
    for ws in [first] + [w for w in book.worksheets() if w.title == sheet_tabs.AMAZON_TAB]:
        if ws.title not in tabs:
            tabs[ws.title] = ws.get_all_values()
    print(f"tab: {sheet.title}")
    values = tabs[sheet.title]
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

    taken = clashes(tabs, NEW_SKU, sheet.title, ROW)
    if taken:
        raise SystemExit(f"ABORT: SKU {NEW_SKU} (or its digits) is already used by {taken} - "
                         "assigning it would recreate the very collision this fixes")
    print(f"new SKU {NEW_SKU}: {code_note(NEW_SKU)}")

    i_sku = col_map["SKU"]
    still_shared = [n for n in range(2, len(values) + 1)
                    if n != ROW and (values[n - 1][i_sku] if i_sku < len(values[n - 1]) else "").strip() == current_sku]
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
