"""One-off, READ-ONLY (2026-09-03): why ~1,000 GTV listings are live on the
front end yet answer "SKU does not exist" on the seller API.

Evidence so far (OnBuy's own dashboard export, 2026-08-19): listings whose
SKU is our 12-digit SKU ROUNDED TO 6 SIGNIFICANT FIGURES - 198651491114 is
stored as 198651000000 (OPC PXM27P7, stock 37, winning the Buy Box) - plus
an older class carrying a prepended zero (102053950247 -> 0102053950247).
Every seller-prepared upload file we still hold (36,570 rows across three
accounts) carries the SKU intact, so the rounding is not in what we sent.

This walks the listings API once and joins it against the sheet's SKU
column under each candidate corruption, which answers two questions:
  1. Are these listings addressable under the CORRUPTED SKU? If yes, the
     platform's database holds the corrupted value (not just its exports),
     and we can drive them by SKU today via the mapping this writes.
  2. How many rows are affected, and which.

Writes sku_rounding_map.csv (true SKU -> OnBuy SKU, OPC, price, stock,
url). Makes NO changes to the sheet, the mirror or OnBuy.
"""
import csv
import json
import os
import time
from collections import Counter

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
MAX_PAGES = int(os.getenv("MAX_PAGES") or "120")
OUT = "sku_rounding_map.csv"


def sigfig(digits, n):
    """The SKU as a %.Ng float would render it - 198651491114 -> 198651000000
    at n=6. That is exactly the damage seen in the platform's records."""
    try:
        v = float(f"%.{n}g" % int(digits))
    except (ValueError, OverflowError):
        return None
    if v != int(v):
        return None
    return str(int(v))


def candidates(sku):
    """Every corrupted form of a true SKU we have evidence for, labelled."""
    out = []
    if sku.isdigit():
        for n in (5, 6, 7):
            c = sigfig(sku, n)
            if c and c != sku:
                out.append((c, f"{n}-significant-figure rounding"))
        out.append(("0" + sku, "prepended zero"))
        stripped = sku.lstrip("0")
        if stripped and stripped != sku:
            out.append((stripped, "stripped leading zero"))
    return out


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = with_retry(lambda: client.open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    values = with_retry(lambda: sheet.get_all_values(), what="sheet read", max_attempts=3)
    headers = values[0]
    idx = {h.strip().lower(): i for i, h in enumerate(headers)}
    i_sku = idx.get("sku")
    i_status = idx.get("sync status")
    i_opc = idx.get("opc")
    if i_sku is None:
        raise SystemExit("no SKU column")

    sheet_skus = {}
    for r in range(2, len(values) + 1):
        row = values[r - 1]
        s = (row[i_sku] if i_sku < len(row) else "").strip()
        if s:
            sheet_skus.setdefault(s, r)
    print(f"sheet rows: {len(values) - 1} | distinct SKUs: {len(sheet_skus)}")

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    live = {}
    offset, limit, pages = 0, 100, 0
    while pages < MAX_PAGES:
        def _page(off=offset):
            resp = onbuy._send("GET", f"{BASE_URL}/listings", what="listings page",
                               params={"site_id": onbuy.site_id, "limit": limit, "offset": off},
                               timeout=60)
            resp.raise_for_status()
            return resp
        body = with_retry(_page, what=f"listings page {offset}", max_attempts=3).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            break
        for it in items:
            s = str(it.get("sku") or "").strip()
            if s:
                live[s] = it
        offset += limit
        pages += 1
        time.sleep(0.3)
    print(f"live listings walked: {len(live)} over {pages} page(s)")
    print("")

    exact = {s: r for s, r in sheet_skus.items() if s in live}
    print(f"sheet SKUs addressable EXACTLY on the API: {len(exact)}")

    matched, classes = [], Counter()
    for s, r in sheet_skus.items():
        if s in live:
            continue
        for cand, label in candidates(s):
            it = live.get(cand)
            if it:
                matched.append((r, s, cand, label, it))
                classes[label] += 1
                break
    print(f"sheet SKUs addressable ONLY under a corrupted SKU: {len(matched)}")
    for label, n in classes.most_common():
        print(f"    {label}: {n}")

    unmatched_live = [s for s in live if s not in sheet_skus]
    print(f"live listings with no exact sheet SKU: {len(unmatched_live)}")
    print("")

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sheet_row", "true_sku", "onbuy_sku", "corruption", "opc",
                    "price", "stock", "sheet_status", "product_url"])
        for r, s, cand, label, it in sorted(matched):
            row = values[r - 1]
            st = (row[i_status] if i_status is not None and i_status < len(row) else "")
            w.writerow([r, s, cand, label, it.get("opc"), it.get("price"),
                        it.get("stock"), st, it.get("product_url")])
    print(f"wrote {OUT} ({len(matched)} row(s))")
    print("")
    for r, s, cand, label, it in sorted(matched)[:15]:
        print(f"  row {r} | true {s} -> onbuy {cand} ({label}) | opc {it.get('opc')} "
              f"| price {it.get('price')} stock {it.get('stock')}")


if __name__ == "__main__":
    main()
