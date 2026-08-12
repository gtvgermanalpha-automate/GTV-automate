"""One-off (2026-08-12): the shifted migration appends expanded the sheet
grid to ~110 columns; the repair cleared the misplaced values but the empty
columns remain, so row 1 carries dozens of blank header cells and
get_all_records() crashes every sync/backfill run with "header row contains
duplicates: ['']". Verify everything beyond the real headers is empty, then
resize the grid back to the header width. DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

FULL_SHEET = "OnBuy_Feed_Master"
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")


def col_letter(n):
    s = ""
    while n >= 0:
        s = chr(n % 26 + 65) + s
        n = n // 26 - 1
    return s


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    sheet = client.open(FULL_SHEET).sheet1

    headers = [str(h).strip() for h in sheet.row_values(1)]
    n_cols = len(headers)
    grid_cols = sheet.col_count
    grid_rows = sheet.row_count
    print(f"real headers: {n_cols} (A..{col_letter(n_cols - 1)}) | grid: {grid_cols} cols x {grid_rows} rows")
    if grid_cols <= n_cols:
        print("grid already matches the headers - nothing to trim")
        return

    # Safety: refuse to cut anything that still holds data.
    beyond = sheet.get(f"{col_letter(n_cols)}1:{col_letter(grid_cols - 1)}{grid_rows}")
    stray = [(r_off + 1, c_off + n_cols, v)
             for r_off, r in enumerate(beyond)
             for c_off, v in enumerate(r) if str(v or "").strip()]
    if stray:
        print(f"REFUSING to trim: {len(stray)} non-empty cell(s) beyond the headers, e.g.:")
        for rn, ci, v in stray[:5]:
            print(f"  {col_letter(ci)}{rn}: {str(v)[:60]!r}")
        raise SystemExit(1)
    print(f"columns {col_letter(n_cols)}..{col_letter(grid_cols - 1)} verified empty")

    if DRY_RUN:
        print(f"DRY RUN - would resize to {n_cols} columns")
        return
    sheet.resize(cols=n_cols)
    print(f"TRIMMED: grid resized to {n_cols} columns")


if __name__ == "__main__":
    main()
