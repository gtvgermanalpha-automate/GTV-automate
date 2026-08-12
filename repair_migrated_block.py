"""One-off (2026-08-12): the semi->Full migration appended 1,308 rows in
three chunks of 500; the user reports chunk 2 (rows 3082-3581) landed
column-shifted (SKU in the Sync Status column, Category around AG,
Supplier URL around AI). Sheets' append table-detection anchored that one
call at the wrong start column. This tool scans the WHOLE appended range,
detects each row's shift empirically by locating the five known values
(digit-run SKU, ebay URL, category path, "Synced", OPC), then rewrites the
row: correct columns filled, misplaced cells cleared. DRY_RUN default on.
The pipeline never processed shifted rows (no URL in the URL column), so
repairing the five cells restores them exactly to as-appended state."""
import json
import os
import re

import gspread
from oauth2client.service_account import ServiceAccountCredentials

FULL_SHEET = "OnBuy_Feed_Master"
SCAN_FROM = int(os.getenv("SCAN_FROM") or "2570")
SCAN_TO = int(os.getenv("SCAN_TO") or "3900")
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")

FIELDS = ["SKU", "Supplier URL", "Category", "Sync Status", "OPC"]


def col_letter(n):
    s = ""
    while n >= 0:
        s = chr(n % 26 + 65) + s
        n = n // 26 - 1
    return s


def classify(values):
    """Locate the five migrated values in a raw row by pattern, regardless
    of which columns they sit in. Returns {field: (index, value)}."""
    found = {}
    for i, v in enumerate(values):
        v = str(v or "").strip()
        if not v:
            continue
        if "ebay." in v.lower() and "http" in v.lower():
            found.setdefault("Supplier URL", (i, v))
        elif v == "Synced":
            found.setdefault("Sync Status", (i, v))
        elif re.fullmatch(r"\d{10,14}", v):
            found.setdefault("SKU", (i, v))
        elif ">" in v:
            found.setdefault("Category", (i, v))
        elif re.fullmatch(r"[A-Z0-9]{6,9}", v):
            found.setdefault("OPC", (i, v))
    return found


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    sheet = client.open(FULL_SHEET).sheet1

    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {h: i for i, h in enumerate(headers)}
    missing = [f for f in FIELDS if f not in col_map]
    if missing:
        raise SystemExit(f"headers missing {missing} - aborting")
    n_cols = len(headers)
    last_needed = max(col_map[f] for f in FIELDS)

    grid = sheet.get(f"A{SCAN_FROM}:{col_letter(max(n_cols, 40) - 1)}{SCAN_TO}")
    ok = repaired = odd = empty = 0
    updates = []
    odd_samples = []
    for off, raw in enumerate(grid):
        rownum = SCAN_FROM + off
        raw = list(raw) + [""] * (max(n_cols, 40) - len(raw))
        nonempty = sum(1 for v in raw if str(v or "").strip())
        if nonempty == 0:
            empty += 1
            continue
        # A processed row (title, prices, description...) has far more
        # than the migration's five cells. Never touch those - the
        # pattern scan below can misread free text (a description
        # containing ">" would masquerade as a Category) and a repair
        # would wipe real data. Sparse rows are the only candidates.
        if nonempty > 7:
            ok += 1
            continue
        found = classify(raw)
        # Healthy migration row: every located value already sits in its
        # own column. Missing OPC/Category is fine (blank by design).
        if found and all(found[f][0] == col_map[f] for f in found):
            ok += 1
            continue
        # Only repair rows that are unmistakably shifted migration rows:
        # SKU + eBay URL + "Synced" all located AND the SKU is NOT in
        # its correct column.
        if not ("SKU" in found and "Supplier URL" in found and "Sync Status" in found
                and found["SKU"][0] != col_map["SKU"]):
            odd += 1
            if len(odd_samples) < 5:
                odd_samples.append((rownum, {f: (v[0], v[1][:40]) for f, v in found.items()}))
            continue
        repaired += 1
        fixed = [""] * n_cols
        for f in FIELDS:
            if f in found:
                fixed[col_map[f]] = found[f][1]
        updates.append({"range": f"A{rownum}:{col_letter(n_cols - 1)}{rownum}",
                        "values": [fixed]})
        if repaired <= 3:
            print(f"  sample row {rownum}: " + ", ".join(
                f"{f}@{col_letter(found[f][0])}" for f in FIELDS if f in found))

    print(f"scan {SCAN_FROM}-{SCAN_TO}: {ok} aligned | {repaired} shifted (repairable) | "
          f"{odd} odd (left alone) | {empty} empty")
    for rn, d in odd_samples:
        print(f"  odd row {rn}: {d}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    CHUNK = 200
    for i in range(0, len(updates), CHUNK):
        sheet.batch_update([dict(u) for u in updates[i:i + CHUNK]],
                           value_input_option="RAW")
    print(f"REPAIRED {len(updates)} row(s): five fields to correct columns, "
          "misplaced cells cleared (full-width row rewrite)")


if __name__ == "__main__":
    main()
