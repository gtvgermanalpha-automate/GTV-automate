"""One-off (2026-08-15): the user deleted the conflicted (wrong-content)
listings from OnBuy via the dashboard so the pipeline can RECREATE them
with correct information. Deletion alone is not enough: the rows still
carry Synced status + an OPC, which the anti-duplicate guard reads as
"already created" and routes to update-only forever. Clear the OnBuy
state on exactly those rows - Sync Status, OPC, Last OnBuy Sync, OnBuy
Product Created/Listing Active/Product ID, and Last Checked Time (so the
oldest-first batch picks them up immediately) - and the next run creates
them fresh, with the activation pass pushing price/stock right after.
SKUs come from the RESET_SKUS env (default: the store's audited conflict
list). DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = "OnBuy_Feed_Master"
DEFAULT_SKUS = (
    "194343045313,194343045481,194343045634,269230877978,690106929116,"
    "694385977035,708130585342,729906639779,883377521480,883423781684,"
    "883439848005,883518622014,883531706449,883562447748,883568760308,"
    "883590578483,883596079731,883640575554"
)
SKUS = {s.strip() for s in (os.getenv("RESET_SKUS") or DEFAULT_SKUS).split(",") if s.strip()}
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
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {h: i for i, h in enumerate(headers)}
    missing = [c for c in CLEAR_COLS if c not in col_map]
    cols = [c for c in CLEAR_COLS if c in col_map]
    if missing:
        print(f"note: sheet lacks {missing} - clearing {cols}")
    rows = sheet.get_all_records()
    updates, found = [], []
    for i, r in enumerate(rows):
        sku = str(r.get("SKU") or "").strip()
        if sku not in SKUS:
            continue
        found.append(sku)
        rownum = i + 2
        for c in cols:
            updates.append({"range": f"{col_letter(col_map[c])}{rownum}", "values": [[""]]})
        print(f"  reset row {rownum} SKU {sku} (status was: {str(r.get('Sync Status') or '')[:40]!r})")
    absent = SKUS - set(found)
    print(f"rows to reset: {len(found)} of {len(SKUS)} requested" +
          (f" | NOT IN SHEET: {sorted(absent)}" if absent else ""))
    if DRY_RUN:
        print("DRY RUN - nothing cleared")
        return
    CHUNK = 200
    for i in range(0, len(updates), CHUNK):
        sheet.batch_update([dict(u) for u in updates[i:i + CHUNK]], value_input_option="RAW")
    print(f"CLEARED {len(cols)} column(s) on {len(found)} row(s) - next runs re-create them fresh")


if __name__ == "__main__":
    main()
