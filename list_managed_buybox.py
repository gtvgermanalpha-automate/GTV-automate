"""One-off, READ-ONLY: print every Competition-tab row the Buy Box engine
actively manages (Action = REPRICE or HELD), with title joined from the
master sheet. Parseable MANAGED| lines."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("OnBuy_Feed_Master")
    comp = None
    for w in ss.worksheets():
        hdr = [str(h).strip().lower() for h in w.row_values(1)]
        if "winning_status" in hdr and "action" in hdr:
            comp = w
            print(f"using tab '{w.title}'")
            break
    if comp is None:
        raise SystemExit("no tab with export headers + decision log found")
    titles = {}
    for r in ss.sheet1.get_all_records():
        sku = str(r.get("SKU") or "").strip()
        if sku:
            titles[sku] = str(r.get("Title") or "").strip()[:70]
    n = 0
    for r in comp.get_all_records():
        action = str(r.get("action") or r.get("Action") or "").strip()
        if action not in ("REPRICE", "HELD"):
            continue
        sku = str(r.get("sku") or "").strip()
        n += 1
        print(f"MANAGED|{sku}|{action}|{r.get('price')}|{r.get('new price') or r.get('New Price') or ''}"
              f"|{r.get('winning_price')}|{r.get('floor') or r.get('Floor') or ''}"
              f"|{r.get('decided at') or r.get('Decided At') or ''}|{titles.get(sku, '')}")
    print(f"managed rows: {n}")


if __name__ == "__main__":
    main()
