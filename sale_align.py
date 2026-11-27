"""Sale re-align (2026-11-27): put every listing that ALREADY carries a sale back at exactly PERCENT off its CURRENT price.

Why: the user applies a store-wide sale in the OnBuy portal (fixed sale prices). Our syncs keep moving the base
prices (Keepa cost moves, profit schedule), so the shown discount drifts - 841983760655 was set at 5% and showed
11% after an overnight price rise. This tool touches ONLY listings that already have a sale price: it re-computes
sale_price = (1 - PERCENT/100) * current price and keeps the listing's own sale window. It never starts a sale on
a listing without one, never ends one, and never touches price or stock.

Env: PERCENT (default 5), DRY_RUN (default 1), TOL (GBP, default 0.01 - smaller drift is left alone).
"""
import os

from onbuy_client import OnBuyClient
from listing_sweep import sweep
from sale_guard import chunks, put_chunk, to_float

PERCENT = float(os.getenv("PERCENT") or "5")
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() in ("1", "yes", "true")
TOL = float(os.getenv("TOL") or "0.01")


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    records, short_pages, _meta, pages = sweep(onbuy)
    print(f"sweep: {len(records)} listings over {pages} page(s) ({len(short_pages)} short page(s))")
    factor = 1.0 - PERCENT / 100.0
    items, right, nosale, skipped = [], 0, 0, 0
    for rec in records:
        rec = rec or {}
        sku = str(rec.get("sku") or "").strip()
        price = to_float(rec.get("price"))
        sale = to_float(rec.get("sale_price"))
        if not sku or sale <= 0:
            nosale += 1
            continue
        if price <= 0:
            skipped += 1
            continue
        wanted = round(price * factor, 2)
        if wanted <= 0 or wanted >= price:
            skipped += 1
            continue
        if abs(wanted - sale) <= TOL:
            right += 1
            continue
        items.append({"sku": sku, "boost_marketing_commission": 0, "sale_price": f"{wanted:.2f}",
                      "sale_start_date": str(rec.get("sale_start_date") or ""),
                      "sale_end_date": str(rec.get("sale_end_date") or "")})
        if len(items) <= 15:
            print(f"ALIGN {sku}: price {price:.2f}, sale {sale:.2f} ({sale / price:.3f}) -> {wanted:.2f}")
    print(f"on a sale and already at {PERCENT}% off: {right} | drifted (to fix): {len(items)} | no sale: {nosale} | skipped (no/invalid price): {skipped}")
    if DRY_RUN:
        print("DRY RUN - nothing sent")
        return
    ok = failed = 0
    for chunk in chunks(items, 500):
        results, error = put_chunk(onbuy, chunk)
        if results is None:
            print(f"chunk of {len(chunk)} failed: {error}")
            failed += len(chunk)
            continue
        for r in results:
            if isinstance(r, dict) and r.get("error"):
                failed += 1
                if failed <= 10:
                    print(f"FAILED {r.get('sku')}: {r.get('error')}")
            else:
                ok += 1
    print(f"DONE: {ok} re-aligned, {failed} failed")


if __name__ == "__main__":
    main()
