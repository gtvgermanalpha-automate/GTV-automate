"""One-off (2026-09-03): prove that the listings the seller API calls
"SKU does not exist" ARE writable under the SKU the platform actually
stored - our SKU rounded to 6 significant figures (see
probe_sku_rounding.py and sku_rounding_map.csv).

Sends each listing its OWN CURRENT price and stock, so a success changes
nothing: it only establishes that the by-SKU endpoint accepts the stored
SKU. SPEC is "sku:price:stock,sku:price:stock,..." taken from the map.
DRY_RUN defaults to yes.
"""
import logging
import os
import sys

from onbuy_client import OnBuyClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SPEC = os.getenv("SPEC") or ""
DRY_RUN = (os.getenv("DRY_RUN") or "yes").strip().lower() not in ("0", "false", "no")


def main():
    triples = []
    for part in [p.strip() for p in SPEC.split(",") if p.strip()]:
        bits = part.split(":")
        if len(bits) != 3:
            print(f"SKIP malformed: {part!r}")
            continue
        sku, price, stock = bits[0].strip(), float(bits[1]), int(bits[2])
        triples.append((sku, price, stock))
    if not triples:
        raise SystemExit("no usable SPEC")

    print(f"{len(triples)} listing(s); each gets its OWN current price/stock (no-op)")
    for sku, price, stock in triples:
        print(f"   {sku} -> price {price} stock {stock}")
    if DRY_RUN:
        print("")
        print("DRY RUN - nothing sent. Re-run with dry_run=no.")
        return

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    results = onbuy.update_listings_by_sku_batch(triples)
    print("")
    print(f"raw results: {len(results) if isinstance(results, list) else results}")
    ok = err = 0
    for r in (results if isinstance(results, list) else []):
        sku = r.get("sku") if isinstance(r, dict) else None
        msg = ""
        if isinstance(r, dict):
            for k in ("error", "errors", "message"):
                if r.get(k):
                    msg = str(r[k])
                    break
        if msg:
            err += 1
            print(f"   FAILED {sku}: {msg[:200]}")
        else:
            ok += 1
            print(f"   OK     {sku}: price {r.get('price')} stock {r.get('stock')}")
    print("")
    print(f"ADDRESSABLE under the stored SKU: {ok} | refused: {err}")
    if ok and not err:
        print("=> the platform's database holds the rounded SKU and accepts writes on it.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
