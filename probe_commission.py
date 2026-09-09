"""READ-ONLY: what OnBuy's API says about commission.

Prints the commission-tier records (GET /v2/commission-tiers, every page)
and a sample of category records (GET /v2/categories) with every key each
carries, so the per-category fee source can be designed from the real
response shape rather than guessed. GET calls only (600/hour quota); no
writes anywhere. Optional CATEGORY_IDS (comma-separated) prints those
categories' full records too.
"""
import json
import os
import time

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import raise_for_status, with_retry

CATEGORY_IDS = [c.strip() for c in (os.getenv("CATEGORY_IDS") or "").split(",") if c.strip()]
SAMPLE = int(os.getenv("SAMPLE") or "5")


def page_all(onbuy, path, limit=100, max_pages=60, **params):
    out, offset = [], 0
    for _ in range(max_pages):
        def _page(off=offset):
            r = onbuy._send("GET", f"{BASE_URL}/{path}", what=f"{path} page",
                            params={"site_id": onbuy.site_id, "limit": limit, "offset": off, **params}, timeout=60)
            raise_for_status(r, what=f"{path} page")
            return r
        body = with_retry(_page, what=f"{path} page {offset}", max_attempts=3).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            if offset == 0:
                print(f"{path}: raw body -> {json.dumps(body)[:800]}")
            break
        out.extend(items)
        if len(items) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return out


def keys_of(records):
    seen = {}
    for r in records:
        for k, v in (r or {}).items():
            seen.setdefault(k, type(v).__name__)
    return seen


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    tiers = page_all(onbuy, "commission-tiers")
    print(f"\n== commission-tiers: {len(tiers)} record(s); keys: {keys_of(tiers)}")
    for t in tiers[:SAMPLE]:
        print("   ", json.dumps(t)[:600])
    if len(tiers) > SAMPLE:
        print("   ... full dump:")
        for t in tiers:
            print("   ", json.dumps(t)[:300])

    cats = page_all(onbuy, "categories", max_pages=2)
    print(f"\n== categories (first pages): {len(cats)} record(s); keys: {keys_of(cats)}")
    for c in cats[:SAMPLE]:
        print("   ", json.dumps(c)[:700])
    hits = [c for c in cats if any("commission" in str(k).lower() or "fee" in str(k).lower() for k in (c or {}))]
    print(f"   records carrying a commission/fee key: {len(hits)} of {len(cats)}")

    for cid in CATEGORY_IDS:
        def _one(cid=cid):
            r = onbuy._send("GET", f"{BASE_URL}/categories/{cid}", what="category view",
                            params={"site_id": onbuy.site_id}, timeout=60)
            raise_for_status(r, what="category view")
            return r
        try:
            body = with_retry(_one, what=f"category {cid}", max_attempts=2).json()
            print(f"\n== category {cid}: {json.dumps(body)[:1500]}")
        except Exception as exc:  # noqa: BLE001 - informational
            print(f"\n== category {cid}: {str(exc)[:200]}")


if __name__ == "__main__":
    main()
