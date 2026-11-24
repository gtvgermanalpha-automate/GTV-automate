"""READ-ONLY: every INACTIVE product row (Status != ACTIVE), for a bulk
removal list (user 2026-11-24: "Delete all inactive products in GTV").

Reads the first product tab + Amazon, writes inactive_rows.csv (tab, row,
SKU, stock, Status, Sync Status head, OnBuy Product Created) and
inactive_skus.txt (unique SKUs, one per line) for the workflow to upload.
Prints the counts by tab and by Sync Status class. No OnBuy, no Keepa,
no sheet writes.
"""
import csv
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)
    tabs = [sheet_tabs.product_sheet(book)]
    try:
        amz = book.worksheet("Amazon")
        if amz.title != tabs[0].title:
            tabs.append(amz)
    except gspread.exceptions.WorksheetNotFound:
        pass

    out, skus, classes = [], [], {}
    for ws in tabs:
        values = with_retry(ws.get_all_values, what=f"read {ws.title}", max_attempts=3)
        ix = {str(h).strip(): i for i, h in enumerate(values[0]) if str(h).strip()}

        def cell(r, k):
            i = ix.get(k)
            return str(r[i]).strip() if i is not None and i < len(r) else ""
        n_inactive = 0
        for n, r in enumerate(values[1:], start=2):
            sku = cell(r, "SKU").replace(",", "").strip()
            if not sku and not cell(r, "Supplier URL"):
                continue
            status = cell(r, "Status").upper()
            if status == "ACTIVE":
                continue
            sync = cell(r, "Sync Status")[:60]
            out.append([ws.title, n, sku, cell(r, "Stock"), status or "(blank)", sync,
                        cell(r, "OnBuy Product Created")])
            if sku:
                skus.append(sku)
            n_inactive += 1
            cls = (sync.split(":")[0][:30] or "(blank sync)") if sync else "(blank sync)"
            classes[(ws.title, cls)] = classes.get((ws.title, cls), 0) + 1
        print(f"{ws.title}: {len(values) - 1} data rows, {n_inactive} INACTIVE")

    with open("inactive_rows.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tab", "row", "sku", "stock", "status", "sync_status", "created"])
        w.writerows(out)
    uniq = list(dict.fromkeys(skus))
    with open("inactive_skus.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(uniq) + "\n")
    dupes = len(skus) - len(uniq)
    print(f"TOTAL INACTIVE rows: {len(out)} | unique SKUs: {len(uniq)} | repeated SKUs: {dupes}")
    for (tab, cls), c in sorted(classes.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {c:5d}  {tab:8s} | {cls}")


if __name__ == "__main__":
    main()
