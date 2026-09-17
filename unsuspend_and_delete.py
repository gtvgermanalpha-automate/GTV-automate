"""One-off (2026-09-17): delete listings OnBuy refuses to delete because
they are suspended ("Listing is suspended" from DELETE /listings/by-sku).

Two moves per explicit SKU list: (1) one batch update pushing a valid
price with STOCK 0 - lifting the price-below-minimum suspension without
ever making anything buyable; (2) the standard per-SKU deletion loop.
Explicit list only, DRY_RUN on by default, raw responses logged. If the
batch update itself is rejected (suspension still locks edits), the
per-item errors say so and those SKUs are reported for a dashboard
delete instead - 39 was the count this was built for.
"""
import logging
import os
import time

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import RateLimitError, raise_for_status, with_retry

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
REVIVE_PRICE = float(os.getenv("REVIVE_PRICE") or "9.99")


def load_skus():
    raw = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]
    path = (os.getenv("SKUS_FILE") or "").strip()
    if path:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                raw.append(line)
    return list(dict.fromkeys(raw))


def main():
    skus = load_skus()
    if not skus:
        raise SystemExit("No SKUs (SKUS / SKUS_FILE) - this tool never runs without an explicit list")
    log.info("SKUs to unsuspend-and-delete: %d%s", len(skus), " (DRY RUN)" if DRY_RUN else "")
    for s in skus[:10]:
        log.info("  %s", s)
    if DRY_RUN:
        log.info("DRY RUN - nothing touched")
        return

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    log.info("STEP 1: batch update price=%.2f stock=0 to lift the suspension", REVIVE_PRICE)
    results = onbuy.update_listings_by_sku_batch([(s, REVIVE_PRICE, 0) for s in skus])
    outcome = {}
    for it in results or []:
        it = it or {}
        s = str(it.get("sku") or "").strip()
        if s:
            outcome[s] = str(it.get("error") or "").strip()
    lifted = [s for s in skus if not outcome.get(s)]
    locked = {s: outcome.get(s, "no answer") for s in skus if outcome.get(s)}
    log.info("update accepted: %d | rejected: %d", len(lifted), len(locked))
    for s, err in list(locked.items())[:15]:
        log.info("  LOCKED %s: %s", s, err[:120])

    time.sleep(5)
    log.info("STEP 2: deleting %d listing(s)", len(lifted))
    deleted = failed = 0
    idx = 0
    while idx < len(lifted):
        sku = lifted[idx]
        try:
            def _do(sku=sku):
                resp = onbuy._send("DELETE", f"{BASE_URL}/listings/by-sku",
                                   what=f"delete({sku})",
                                   json={"site_id": onbuy.site_id, "skus": [sku]})
                log.info("DELETE %s raw [%s]: %s", sku, resp.status_code, resp.text[:300])
                raise_for_status(resp, what=f"delete({sku})")
                return resp
            with_retry(_do, what=f"delete({sku})", max_attempts=3)
            deleted += 1
            log.info("DELETED %s", sku)
            idx += 1
        except RateLimitError:
            log.warning("burst limit at %d/%d - waiting 90s", idx, len(lifted))
            time.sleep(90)
            continue
        except Exception as exc:
            failed += 1
            log.warning("DELETE %s failed - %s", sku, str(exc)[:200])
            idx += 1
        time.sleep(0.5)
    log.info("DONE: %d deleted, %d delete-failed, %d still locked (dashboard needed)",
             deleted, failed, len(locked))
    if locked:
        log.info("Dashboard list: %s", ", ".join(sorted(locked)))


if __name__ == "__main__":
    main()
