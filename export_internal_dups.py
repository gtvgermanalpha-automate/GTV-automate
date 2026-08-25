"""One-off (2026-08-25): export GTV's internal duplicate-SKU groups among
the ORIGINAL (pre-import-block) sheet rows as a CSV for the team - two or
more rows whose SKU cells hold the same value once numericised (leading
zeros/format differences collapse). These pre-date the catalog import (the
known 8-headphone-SKU problem, actually 127 groups). One CSV line per row,
grouped, so the team can pick which copy of each pair to keep. Read-only."""
import csv
import json
import os
from collections import defaultdict

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
BLOCK_START = int(os.getenv("BLOCK_START") or "999999")
OUT = os.getenv("OUT") or "internal_dups.csv"


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    headers = with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)
    sku_col = {h.strip(): i for i, h in enumerate(headers)}["SKU"] + 1
    disp = with_retry(lambda: sheet.col_values(sku_col), what="sku display col", max_attempts=3)

    groups = defaultdict(list)
    for i, r in enumerate(rows):
        rownum = i + 2
        if rownum >= BLOCK_START:
            break
        sku = str(r.get("SKU") or "").strip()
        if sku:
            groups[sku].append((rownum, r))
    dup_groups = {s: g for s, g in groups.items() if len(g) > 1}
    print(f"pre-block rows: {sum(len(g) for g in groups.values())} | duplicate-SKU groups: {len(dup_groups)}")

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Group", "SKU (as stored)", "Cell shows", "Sheet Row", "Title",
                    "Sync Status", "Has Supplier URL", "Cost Price (£)", "Selling Price (£)", "Stock"])
        for gi, (sku, g) in enumerate(sorted(dup_groups.items()), 1):
            for rownum, r in sorted(g):
                shown = disp[rownum - 1] if rownum - 1 < len(disp) else ""
                w.writerow([gi, sku, shown, rownum,
                            str(r.get("Title") or "").strip(),
                            str(r.get("Sync Status") or "").strip(),
                            "yes" if str(r.get("Supplier URL") or "").strip() else "no",
                            str(r.get("Cost Price (£)") or "").strip(),
                            str(r.get("Selling Price (£)") or "").strip(),
                            str(r.get("Stock") or "").strip()])
    print(f"exported {sum(len(g) for g in dup_groups.values())} row(s) in {len(dup_groups)} group(s) -> {OUT}")


if __name__ == "__main__":
    main()
