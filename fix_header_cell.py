"""One-off, surgical: restore a single broken HEADER cell (row 1).

2026-09-22: a stray keystroke turned the Amazon tab's B1 from "Title"
into "desk" while rows were being pasted, and the header guard (rightly)
refused every run. A full-row restore from sheet_headers.csv would be
wrong here - the Amazon tab's column ORDER differs from the eBay tab's -
so this fixes exactly one named cell, printing the old value first.

Env: SHEET_TAB (worksheet; empty = first tab), HEADER_CELL (e.g. "B1" -
row 1 only, enforced), HEADER_VALUE (the exact header name to write).
"""
import json
import os
import re

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

SHEET_NAME = "OnBuy_Feed_Master"


def main():
    cell = (os.getenv("HEADER_CELL") or "").strip().upper()
    value = (os.getenv("HEADER_VALUE") or "").strip()
    if not re.fullmatch(r"[A-Z]{1,2}1", cell):
        raise SystemExit(f"HEADER_CELL must be a row-1 cell like B1, got {cell!r}")
    if not value:
        raise SystemExit("HEADER_VALUE is required")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)
    tab_name = (os.getenv("SHEET_TAB") or "").strip()
    tab = book.sheet1 if not tab_name else book.worksheet(tab_name)
    old = tab.acell(cell).value
    print(f"[{tab.title}] {cell}: {old!r} -> {value!r}")
    with_retry(lambda: tab.update(cell, [[value]], value_input_option="RAW"),
               what="header cell write", max_attempts=3)
    print("done - re-run the sync")


if __name__ == "__main__":
    main()
