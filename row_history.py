"""READ-ONLY (2026-11-05): print the cells of named SKUs as they stood in earlier Drive revisions of the sheet.

Built for the GTV order T6R9KFW case: the zero-stock layers all missed SKU 175321526932 on 2026-11-04 and only the sheet's own
history can show what the row looked like when each layer ran. Uses the revision list/export code of restore_sku_column.py. Prints
no cost cells. Writes nothing. Env: SKUS (comma-separated), REVISIONS (comma-separated Drive revision ids), TAB (default Amazon).
"""
import io
import json
import os

import openpyxl

import restore_sku_column as rsc

SHOW = ["Stock", "Status", "Sync Status", "OnBuy Product Created", "OnBuy Listing Active", "Selling Price (£)", "Price Check Flag",
        "Last Updated", "Last Checked Time", "Last OnBuy Sync", "OPC"]


def sku_key(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).replace(",", "").strip()


def main():
    wanted = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]
    revids = [s.strip() for s in (os.getenv("REVISIONS") or "").split(",") if s.strip()]
    tabs = [t.strip() for t in (os.getenv("TAB") or "Amazon").split(",") if t.strip()]
    creds, gc = rsc._clients()
    book = gc.open(rsc.SHEET_NAME)
    revs = {r["id"]: r for r in rsc.list_revisions(creds, book.id)}
    for rid in revids:
        rev = revs.get(rid)
        if not rev:
            print(f"REVISION {rid}: not in the list")
            continue
        blob = rsc.fetch_export(creds, rev)
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
        print(f"=== REVISION {rid} modified {rev['modifiedTime']} by {rsc.who(rev)}; tabs {wb.sheetnames[:8]}")
        for tab in tabs:
            if tab not in wb.sheetnames:
                print(f"  tab {tab!r} not in this revision")
                continue
            it = wb[tab].iter_rows(values_only=True)
            header = [str(h).strip() if h is not None else "" for h in next(it, ())]
            ix = {h: i for i, h in enumerate(header) if h}
            if "SKU" not in ix:
                print(f"  tab {tab}: no SKU header in this revision")
                continue
            found = {s: [] for s in wanted}
            for n, row in enumerate(it, start=2):
                k = sku_key(row[ix["SKU"]]) if ix["SKU"] < len(row) else ""
                if k in found:
                    found[k].append((n, row))
            for s in wanted:
                if not found[s]:
                    print(f"  {tab}: SKU {s}: not in this revision")
                for n, row in found[s]:
                    cells = {c: ("" if c not in ix or ix[c] >= len(row) or row[ix[c]] is None else str(row[ix[c]])[:38]) for c in SHOW}
                    print(f"  {tab} row {n} SKU {s} | " + " | ".join(f"{c}: {v}" for c, v in cells.items()))


if __name__ == "__main__":
    main()
