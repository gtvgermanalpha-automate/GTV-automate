"""One-off (2026-08-25, user-approved SKU repair): resolve the 127
census-pair groups. Each group holds the SAME product twice: the full
supplier-linked row whose SKU cell lost its leading zero (every by-SKU push
targets a SKU OnBuy doesn't know - stuck "Awaiting OnBuy go-live" forever)
and an 08-12 migration census row (no Supplier URL) whose displayed SKU is
the REAL live listing's SKU. Repair per conforming group:
  - write the census row's displayed SKU into the full row's SKU cell
    (value_input_option RAW so the leading zero survives as text - the
    displayed-SKU overlay shipped alongside makes the pipeline read it);
  - set Sync Status "Synced" + blank Last OnBuy Sync so the next sync run's
    activation pass adopts the live listing in one batch;
  - delete the census row (descending order);
  - delete the stale zero-less mirror row (key != new SKU only).
Guards: exactly-2 groups with one URL row and one non-URL row only;
protected SKUs skipped; a target SKU already used by a third row skips the
group. Identical-display pairs (the old 8-headphone class) repair as: keep
URL row, drop the twin, mirror untouched. DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import supabase_db
from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = "OnBuy_Feed_Master"


def col_letter(idx0):
    s = ""
    idx0 += 1
    while idx0:
        idx0, rem = divmod(idx0 - 1, 26)
        s = chr(65 + rem) + s
    return s


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    headers = [str(h).strip() for h in with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)]
    col = {h: i for i, h in enumerate(headers)}
    for need in ("SKU", "Sync Status", "Last OnBuy Sync", "Supplier URL"):
        if need not in col:
            raise SystemExit(f"missing column {need!r}")
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    disp = with_retry(lambda: sheet.col_values(col["SKU"] + 1), what="sku display col", max_attempts=3)

    protected = set()
    pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "protected_skus.txt")
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as fh:
            protected = {ln.split("#", 1)[0].strip() for ln in fh if ln.split("#", 1)[0].strip()}

    def display(rownum):
        return str(disp[rownum - 1]).strip() if rownum - 1 < len(disp) else ""

    groups = {}
    display_use = {}
    for i, r in enumerate(rows):
        rownum = i + 2
        key = str(r.get("SKU") or "").strip()
        d = display(rownum)
        if key:
            groups.setdefault(key, []).append((rownum, r))
        if d:
            display_use.setdefault(d, []).append(rownum)

    updates, deletes, mirror_purge = [], [], []
    repaired = skipped = 0
    for key, g in sorted(groups.items()):
        if len(g) < 2:
            continue
        if len(g) > 2:
            print(f"SKIP {key}: {len(g)} rows - not a pair"); skipped += 1
            continue
        with_url = [(rn, r) for rn, r in g if str(r.get("Supplier URL") or "").strip()]
        no_url = [(rn, r) for rn, r in g if not str(r.get("Supplier URL") or "").strip()]
        if len(with_url) != 1 or len(no_url) != 1:
            print(f"SKIP {key}: rows {[rn for rn, _ in g]} - URL pattern not one/one"); skipped += 1
            continue
        full_rn, _ = with_url[0]
        sparse_rn, _ = no_url[0]
        target = display(sparse_rn)
        old = display(full_rn)
        if not target:
            print(f"SKIP {key}: sparse row {sparse_rn} has no displayed SKU"); skipped += 1
            continue
        if target in protected or old in protected:
            print(f"SKIP {key}: protected"); skipped += 1
            continue
        others = [rn for rn in display_use.get(target, []) if rn not in (full_rn, sparse_rn)]
        if others:
            print(f"SKIP {key}: target {target} also on row(s) {others}"); skipped += 1
            continue
        print(f"REPAIR rows {full_rn}(full,{old!r})<-{sparse_rn}(census,{target!r}): SKU:={target}, Synced, sync cleared, census row deleted")
        updates.append((f"{col_letter(col['SKU'])}{full_rn}", [[target]]))
        updates.append((f"{col_letter(col['Sync Status'])}{full_rn}", [["Synced"]]))
        updates.append((f"{col_letter(col['Last OnBuy Sync'])}{full_rn}", [[""]]))
        deletes.append(sparse_rn)
        if key != target:
            mirror_purge.append(key)
        repaired += 1
    print(f"pairs to repair: {repaired} | skipped: {skipped} | cell writes: {len(updates)} | rows to delete: {len(deletes)} | mirror keys to purge: {len(mirror_purge)}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    if updates:
        pairs = list(updates)
        for c in range(0, len(pairs), 400):
            chunk = pairs[c:c + 400]
            with_retry(lambda ch=chunk: sheet.batch_update(
                [{"range": rg, "values": v} for rg, v in ch], value_input_option="RAW"),
                what=f"repair writes {c}", max_attempts=3)
            print(f"written {min(c + 400, len(pairs))}/{len(pairs)}")
    if deletes:
        requests = [{"deleteDimension": {"range": {
            "sheetId": sheet.id, "dimension": "ROWS",
            "startIndex": rn - 1, "endIndex": rn}}} for rn in sorted(deletes, reverse=True)]
        for c in range(0, len(requests), 400):
            chunk = requests[c:c + 400]
            with_retry(lambda ch=chunk: sheet.spreadsheet.batch_update({"requests": ch}),
                       what=f"census deletes {c}", max_attempts=3)
            print(f"deleted {min(c + 400, len(requests))}/{len(requests)}")
    if mirror_purge:
        supabase_db.delete_products(mirror_purge)
        print(f"purged {len(mirror_purge)} stale zero-less mirror row(s)")
    print(f"repaired {repaired} pair(s)")


if __name__ == "__main__":
    main()
