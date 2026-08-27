"""One-off (2026-08-25): user reports Supplier URL cells gone from sheet
rows 322-817. Diagnose and restore from the Supabase mirror (full-row
upserts keep every processed row's Supplier URL). For each row in the range
with an empty Supplier URL, look up the row's DISPLAYED SKU text (leading
zeros intact - get_all_records numericises them away) in the mirror:
  - mirror hit with a URL  -> restorable (write the URL back);
  - mirror miss            -> the SKU was never processed by the pipeline
    (e.g. an 08-12 migration census row) - it never had a link to lose.
Also maps every contiguous empty-URL band across the whole pre-import sheet
so the real extent of any wipe is visible, and reports whether Cost Price /
Category are empty too on restorable rows (a whole-row wipe would show
there). Restores ONLY the Supplier URL column. DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import supabase_db
from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = "OnBuy_Feed_Master"
ROW_START = int(os.getenv("ROW_START") or "322")
ROW_END = int(os.getenv("ROW_END") or "817")


def col_letter(idx):
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    headers = with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)
    col = {h.strip(): i for i, h in enumerate(headers)}
    url_col = col["Supplier URL"]
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    disp = with_retry(lambda: sheet.col_values(col["SKU"] + 1), what="sku display col", max_attempts=3)

    def display_sku(rownum):
        return str(disp[rownum - 1]).strip() if rownum - 1 < len(disp) else ""

    # Full-sheet picture: contiguous empty-URL bands among rows that have a SKU.
    bands, cur = [], None
    for i, r in enumerate(rows):
        rownum = i + 2
        sku = display_sku(rownum)
        if not sku:
            continue
        empty = not str(r.get("Supplier URL") or "").strip()
        if empty and cur is None:
            cur = [rownum, rownum]
        elif empty:
            cur[1] = rownum
        elif cur is not None:
            bands.append(tuple(cur)); cur = None
    if cur is not None:
        bands.append(tuple(cur))
    big = [(a, b) for a, b in bands if b - a + 1 >= 20]
    print(f"empty-Supplier-URL bands >=20 rows: {[(a, b, b - a + 1) for a, b in big]}")

    # The requested range.
    empties = []
    for i, r in enumerate(rows):
        rownum = i + 2
        if rownum < ROW_START or rownum > ROW_END:
            continue
        sku = display_sku(rownum)
        if sku and not str(r.get("Supplier URL") or "").strip():
            empties.append((rownum, sku, r))
    print(f"range {ROW_START}-{ROW_END}: {ROW_END - ROW_START + 1} rows | empty Supplier URL: {len(empties)}")

    mirror = {}
    sku_list = [s for _, s, _ in empties]
    for c in range(0, len(sku_list), 100):
        mirror.update(supabase_db.fetch_full_rows(sku_list[c:c + 100]) or {})
    restorable, misses = [], []
    for rownum, sku, r in empties:
        m = mirror.get(sku)
        murl = str((m or {}).get("Supplier URL") or "").strip()
        if murl:
            other = []
            if not str(r.get("Cost Price (£)") or "").strip():
                other.append("cost empty")
            if not str(r.get("Category") or "").strip():
                other.append("category empty")
            restorable.append((rownum, sku, murl, ", ".join(other) or "other fields intact"))
        else:
            misses.append((rownum, sku, str(r.get("Sync Status") or "").strip()))
    print(f"restorable from mirror: {len(restorable)} | no mirror URL (never processed): {len(misses)}")
    for rownum, sku, murl, other in restorable[:10]:
        print(f"  RESTORE row {rownum} SKU {sku} <- {murl[:60]} | {other}")
    for rownum, sku, st in misses[:10]:
        print(f"  NO-MIRROR row {rownum} SKU {sku} | status {st[:40]!r}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    if not restorable:
        print("nothing to restore")
        return
    pairs = [(f"{col_letter(url_col)}{rownum}", [[murl]]) for rownum, _, murl, _ in restorable]
    for c in range(0, len(pairs), 400):
        chunk = pairs[c:c + 400]
        with_retry(lambda ch=chunk: sheet.batch_update([{"range": rg, "values": v} for rg, v in ch]),
                   what=f"restore batch {c}", max_attempts=3)
        print(f"written {min(c + 400, len(pairs))}/{len(pairs)}")
    print(f"restored {len(restorable)} Supplier URL cell(s) from the mirror")


if __name__ == "__main__":
    main()
