"""READ-ONLY (2026-11-06): ask OnBuy's catalogue whether a product already carries each barcode.

A SKU whose digits pass the GTIN check is sent to OnBuy as the product code on create, and an existing catalogue product with that
code takes the new listing (its page, not ours) - 63 GTV Christmas trees landed on desk pages that way on 11-05. This looks each
code up first (GET /v2/products, filter by product_code) and prints whether it is FREE or TAKEN (with the product's OPC and name).
GET only; writes nothing. Env: CODES (comma-separated SKUs; the digits are looked up), FIELD (default product_code).
"""
import json
import os
import re
import time

from onbuy_client import BASE_URL, OnBuyClient

CODES = [c.strip() for c in (os.getenv("CODES") or "").split(",") if c.strip()]
FIELD = (os.getenv("FIELD") or "product_code").strip()


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    free = taken = errors = 0
    for i, sku in enumerate(CODES):
        code = re.sub(r"\D", "", sku)
        params = {"site_id": onbuy.site_id, "filter[query]": code, "filter[field]": FIELD, "limit": 5, "offset": 0}
        for _try in range(4):
            r = onbuy._send("GET", f"{BASE_URL}/products", what="product search", params=params, timeout=60)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(30)
                continue
            break
        if r.status_code != 200:
            errors += 1
            print(f"ERROR|{sku}|HTTP {r.status_code}|{r.text[:200]}")
            continue
        body = r.json()
        items = body.get("results") if isinstance(body, dict) else body
        items = items if isinstance(items, list) else []
        if i == 0:
            print("first answer keys:", sorted(body.keys()) if isinstance(body, dict) else type(body),
                  "| item keys:", sorted(items[0].keys()) if items and isinstance(items[0], dict) else "-")
        exact = []
        for it in items:
            codes = json.dumps(it.get("product_codes") or it.get("product_code") or it.get("ean") or "")
            exact.append((it.get("opc"), str(it.get("name") or it.get("product_name") or "")[:70], codes[:60]))
        if items:
            taken += 1
            print(f"TAKEN|{sku}|{len(items)} product(s)|" + " || ".join(f"{o} {n} {c}" for o, n, c in exact[:2]))
        else:
            free += 1
            print(f"FREE|{sku}")
        time.sleep(0.4)
    print(f"DONE: {len(CODES)} code(s): {free} free, {taken} taken, {errors} errors")


if __name__ == "__main__":
    main()
