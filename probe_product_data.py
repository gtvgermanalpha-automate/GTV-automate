"""One-off, READ-ONLY (2026-08-19): me-too system feasibility probe.
For each given OPC, hit every plausible product/offer endpoint and print
the verbatim responses - we need to learn exactly which fields OnBuy
exposes for: the product's EAN/product codes, the listing owner's (Buy
Box) price, and the competing sellers' offers. Changes nothing."""
import json
import logging
import os
import re

import requests

from onbuy_client import BASE_URL, OnBuyClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

PRODUCT_IDS = [s.strip() for s in (os.getenv("PROBE_IDS") or "280175406,279891425").split(",") if s.strip()]


def try_get(onbuy, label, url, params=None):
    try:
        resp = onbuy._send("GET", url, what=label, params=params or {}, timeout=60)
        log.info("PROBE %s [%s]: %s", label, resp.status_code, resp.text[:1500])
    except Exception as exc:
        log.info("PROBE %s FAILED: %s", label, str(exc)[:200])


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    # Public product page: the seller API exposes no competitor offers, so
    # the Buy Box owner's price must come from the page itself. Check for
    # structured data (JSON-LD offers) and visible price markup.
    for url in [u.strip() for u in (os.getenv("PROBE_URLS") or "").split(",") if u.strip()]:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            log.info("PAGE %s [%s] %d bytes", url[:80], r.status_code, len(r.text))
            for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
                block = m.group(1).strip()
                if '"offers"' in block or '"price"' in block:
                    log.info("JSONLD: %s", block[:1200])
            for m in re.finditer(r'"price"\s*:\s*"?([0-9.]+)"?', r.text[:200000]):
                log.info("PRICE-MARK: %s", m.group(1))
                break
        except Exception as exc:
            log.info("PAGE FAILED: %s", str(exc)[:200])

    # Export-endpoint sweep: the dashboard's "Queue Custom Export" clearly
    # has backend endpoints - check whether the seller API exposes them.
    for path in ["exports", "listings/exports", "exports/listings",
                 "inventory/exports", "reports", "listings/export"]:
        try_get(onbuy, f"GET /{path}", f"{BASE_URL}/{path}", {"site_id": onbuy.site_id, "limit": 2})

    # Arbitrary GET paths, e.g. "categories?filter[parent_id]=3472" (2026-08-21:
    # OnBuy rejected category IDs 3472/13705 as "not a lowest level category",
    # so the tree grew children our CSV does not have - find them).
    for pth in [x.strip() for x in (os.getenv("PROBE_PATHS") or "").split(",") if x.strip()]:
        try_get(onbuy, f"GET /{pth}", f"{BASE_URL}/{pth}", {"site_id": onbuy.site_id, "limit": 100})

    # Buy Box probe (2026-08-21): OnBuy support named GET /v2/listings/check-winning.
    wsk = [x.strip() for x in (os.getenv("PROBE_WINNING_SKUS") or "").split(",") if x.strip()]
    if wsk:
        try:
            res = onbuy.check_winning(wsk)
            log.info("CHECK-WINNING parsed: %s", json.dumps(res)[:3000] if res is not None else None)
        except Exception as exc:
            log.info("CHECK-WINNING FAILED: %s", str(exc)[:400])

    for pid in PRODUCT_IDS:
        log.info("======== product_id %s ========", pid)
        try_get(onbuy, f"products/{pid}", f"{BASE_URL}/products/{pid}",
                {"site_id": onbuy.site_id})
        try_get(onbuy, f"products?filter[product_id]={pid}", f"{BASE_URL}/products",
                {"site_id": onbuy.site_id, "filter[product_id]": pid, "limit": 5})
        try_get(onbuy, f"products/{pid}/listings", f"{BASE_URL}/products/{pid}/listings",
                {"site_id": onbuy.site_id})
        try_get(onbuy, f"listings?filter[product_id]={pid}", f"{BASE_URL}/listings",
                {"site_id": onbuy.site_id, "filter[product_id]": pid, "limit": 5})


if __name__ == "__main__":
    main()
