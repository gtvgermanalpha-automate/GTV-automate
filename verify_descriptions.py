"""READ-ONLY: read back what OnBuy actually holds as each product's
description, and say whether seller junk is still in it.

The re-push reports "N accepted", which is the platform acknowledging the
request - not proof of what a shopper now sees. The seller API exposes no
description field on the endpoints we use, so this tries, in order:

  1. GET /v2/products/<opc> and the filtered forms, in case a description
     is returned there (recorded verbatim either way, so the next person
     knows what the API does and does not give us).
  2. The public product page, fetched plainly and identified honestly.
     If the site answers 403 to an automated request, that is REPORTED,
     never worked around.

Whatever source yields text is scanned with the same junk rules the
sanitizer enforces, so the verdict per SKU is "clean", "still has junk"
(with the offending snippet) or "could not read".

Writes verify_descriptions.csv. Changes nothing.
"""
import csv
import json
import os
import re
import time

import requests

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import raise_for_status, with_retry

SKUS = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]
MAX_PAGES = int(os.getenv("MAX_PAGES") or "120")
FETCH_PAGES = (os.getenv("FETCH_PAGES") or "yes").strip().lower() in ("1", "yes", "true")
OUT = "verify_descriptions.csv"

JUNK = [
    ("price", re.compile(r"(£|GBP|\$)\s?\d|\d\s?(£|GBP)", re.I)),
    ("shipping/returns", re.compile(r"\b(shipping|postage|dispatch(ed)?|deliver(y|ed)|returns?|refund)\b", re.I)),
    ("store/menu", re.compile(r"\b(our (ebay )?store|store (home|categories|menu)|visit (our|us)|shop (now|categories)|about us|contact us|feedback|payment)\b", re.I)),
    ("link", re.compile(r"(https?://|www\.|<a\s|href=)", re.I)),
    ("ebay", re.compile(r"\bebay\b", re.I)),
    ("branding", re.compile(r"\b(powered by|frooition|all rights reserved|©|&copy;|volo origin|total digital stores)\b", re.I)),
    ("seller voice", re.compile(r"\b(we ship|we offer|our range|please contact us|money.?back)\b", re.I)),
]


def scan(text):
    plain = re.sub(r"<[^>]+>", " ", text or "")
    hits = []
    for label, rx in JUNK:
        m = rx.search(text if label == "link" else plain)
        if m:
            src = text if label == "link" else plain
            s = max(0, m.start() - 40)
            snippet = re.sub(r"\s+", " ", src[s:m.end() + 60]).strip()
            hits.append(f"{label}: ...{snippet}...")
    return hits


def listings_map(onbuy):
    """sku -> (opc, product_url) for the whole account."""
    out, offset, pages = {}, 0, 0
    while pages < MAX_PAGES:
        def _page(off=offset):
            r = onbuy._send("GET", f"{BASE_URL}/listings", what="listings page",
                            params={"site_id": onbuy.site_id, "limit": 100, "offset": off}, timeout=60)
            raise_for_status(r, what="listings page")
            return r
        body = with_retry(_page, what=f"listings page {offset}", max_attempts=4).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            break
        for it in items:
            it = it or {}
            sku = str(it.get("sku") or "").strip()
            if sku and sku not in out:
                out[sku] = (str(it.get("opc") or "").strip(), str(it.get("product_url") or "").strip())
        offset += 100
        pages += 1
        time.sleep(0.3)
    return out


def description_from_api(onbuy, opc):
    """Try the product endpoints; return (text, which_endpoint) or (None, note)."""
    attempts = [
        (f"{BASE_URL}/products/{opc}", {"site_id": onbuy.site_id}),
        (f"{BASE_URL}/products", {"site_id": onbuy.site_id, "filter[opc]": opc, "limit": 1}),
        (f"{BASE_URL}/products", {"site_id": onbuy.site_id, "opc": opc, "limit": 1}),
    ]
    notes = []
    for url, params in attempts:
        try:
            resp = onbuy._send("GET", url, what="product read", params=params, timeout=45)
            if resp.status_code >= 400:
                notes.append(f"{url.rsplit('/', 1)[-1]}:{resp.status_code}")
                continue
            body = resp.json()
        except Exception as exc:
            notes.append(f"{url.rsplit('/', 1)[-1]}:{str(exc)[:40]}")
            continue
        found = _dig_description(body)
        if found:
            return found, url
        notes.append(f"{url.rsplit('/', 1)[-1]}:no description field")
    return None, "; ".join(notes)


def _dig_description(node, depth=0):
    if depth > 6:
        return None
    if isinstance(node, dict):
        for key in ("description", "product_description", "long_description"):
            val = node.get(key)
            if isinstance(val, str) and val.strip():
                return val
        for val in node.values():
            got = _dig_description(val, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for val in node:
            got = _dig_description(val, depth + 1)
            if got:
                return got
    return None


def description_from_page(url):
    """Plain fetch of the public page. A 403 means the site declines
    automated requests - reported, not circumvented."""
    if not url:
        return None, "no product_url"
    try:
        r = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (compatible; OnBuySellerBot/1.0; seller-owned listing check)"})
    except requests.exceptions.RequestException as exc:
        return None, f"page fetch failed: {str(exc)[:60]}"
    if r.status_code == 403:
        return None, "page 403 (site declines automated requests)"
    if r.status_code >= 400:
        return None, f"page {r.status_code}"
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        got = _dig_description(data)
        if got:
            return got, "page JSON-LD"
    m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', r.text, re.I)
    if m:
        return m.group(1), "page meta description"
    return None, "page had no description field"


def main():
    if not SKUS:
        raise SystemExit("SKUS required")
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    live = listings_map(onbuy)
    print(f"live listings walked: {len(live)} | SKUs to verify: {len(SKUS)}")

    rows, clean, dirty, unread = [], 0, 0, 0
    for sku in SKUS:
        opc, url = live.get(sku, ("", ""))
        if not opc:
            rows.append({"sku": sku, "opc": "", "verdict": "not on the account",
                         "source": "", "junk": "", "sample": ""})
            unread += 1
            continue
        text, source = description_from_api(onbuy, opc)
        if not text and FETCH_PAGES:
            text, source = description_from_page(url)
        if not text:
            rows.append({"sku": sku, "opc": opc, "verdict": "could not read",
                         "source": source, "junk": "", "sample": ""})
            unread += 1
            continue
        hits = scan(text)
        rows.append({"sku": sku, "opc": opc,
                     "verdict": "still has junk" if hits else "clean",
                     "source": source, "junk": "; ".join(h.split(":")[0] for h in hits),
                     "sample": (hits[0] if hits else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:160])})
        if hits:
            dirty += 1
        else:
            clean += 1
        time.sleep(0.4)

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["sku", "opc", "verdict", "source", "junk", "sample"])
        w.writeheader()
        w.writerows(rows)

    print("")
    print(f"VERDICT  clean: {clean} | still has junk: {dirty} | could not read: {unread}")
    for r in rows:
        print(f"  {r['sku']:<14} {r['opc'] or '-':<9} {r['verdict']:<18} {r['source'][:60]}")
        if r["verdict"] == "still has junk":
            print(f"      {r['sample'][:200]}")
    if unread and not clean and not dirty:
        print("")
        print("NOTE: nothing could be read back. The seller API exposes no description "
              "on these endpoints and the public pages decline automated requests, so "
              "the re-push's per-product acceptance is the only confirmation available.")


if __name__ == "__main__":
    main()
