"""One-off, READ-ONLY (2026-08-19): me-too system feasibility probe.
For each given OPC, hit every plausible product/offer endpoint and print
the verbatim responses - we need to learn exactly which fields OnBuy
exposes for: the product's EAN/product codes, the listing owner's (Buy
Box) price, and the competing sellers' offers. Changes nothing."""
import logging
import os

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
