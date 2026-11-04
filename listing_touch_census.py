"""READ-ONLY (2026-11-04): WHEN were the account's listings last touched on OnBuy?

Why: an order landed on a listing the sheet had marked out of stock for ten hours and that OnBuy still sold (GTV SKU 175321526932, order
T6R9KFW); nobody edited it by hand and none of our runs sent it a stock above 0. If something OUTSIDE our automation raised the stock, other
listings carry the same `updated_at` stamp in a window when none of our runs pushed. This sweeps /v2/listings once (no writes anywhere) and
prints, per bucket of BUCKET_MIN minutes over the last HOURS hours (UTC), how many listings were last touched then, split by stock 0 / above 0,
so the buckets can be laid next to the run logs. Our own passes leave tall, narrow spikes at their start times (oversell guard, audit FIX,
per-row pushes); a bulk writer of someone else shows up as a spike at a time no run of ours was pushing.
Env: HOURS (default 16), BUCKET_MIN (default 10), MIN_SHOW (smallest bucket printed, default 5), TARGET_SKUS (comma separated: print their
updated_at / stock), WINDOW_FROM / WINDOW_TO ("YYYY-MM-DD HH:MM:SS" UTC: also list in-stock listings last touched inside it, SHOW_MAX of them).
Prints SKUs, stock and times only - no cost, no buyer data."""
import os
import time
from datetime import datetime, timedelta, timezone

HOURS = float(os.getenv("HOURS") or "16")
BUCKET_MIN = int(os.getenv("BUCKET_MIN") or "10")
MIN_SHOW = int(os.getenv("MIN_SHOW") or "5")
SHOW_MAX = int(os.getenv("SHOW_MAX") or "60")
TARGETS = [s.strip() for s in (os.getenv("TARGET_SKUS") or "").split(",") if s.strip()]
WINDOW_FROM = (os.getenv("WINDOW_FROM") or "").strip()
WINDOW_TO = (os.getenv("WINDOW_TO") or "").strip()


def parse_stamp(value):
    """'2026-11-04 10:53:33' -> naive UTC datetime, None when it is not a stamp."""
    try:
        return datetime.strptime(str(value or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def buckets(items, now, hours=HOURS, bucket_min=BUCKET_MIN):
    """{bucket start: [touched, stock0, stock_above_0]} for listings whose updated_at lies within the last `hours` before `now`."""
    start = now - timedelta(hours=hours)
    out = {}
    for it in items:
        ts = parse_stamp((it or {}).get("updated_at"))
        if ts is None or ts < start or ts > now:
            continue
        minute = (ts.minute // bucket_min) * bucket_min
        key = ts.replace(minute=minute, second=0, microsecond=0)
        row = out.setdefault(key, [0, 0, 0])
        row[0] += 1
        row[1 if to_int((it or {}).get("stock")) == 0 else 2] += 1
    return out


def sweep(onbuy, base_url):
    """Every listing of the account (the audit's own paging: patient on 429 / 5xx, a short page is confirmed once)."""
    from retry_utils import with_retry
    items, off = [], 0
    while True:
        def page(o=off):
            for attempt in range(6):
                r = onbuy._send("GET", f"{base_url}/listings", what=f"listings page {o}",
                                params={"site_id": onbuy.site_id, "limit": 100, "offset": o}, timeout=60)
                if r.status_code in (429, 500, 502, 503) and attempt < 5:
                    print(f"listings page {o}: HTTP {r.status_code} - waiting 120s", flush=True)
                    time.sleep(120)
                    continue
                r.raise_for_status()
                return r
        body = with_retry(page, what=f"listings page {off}", max_attempts=3).json()
        batch = body.get("results") if isinstance(body, dict) else body
        if isinstance(batch, list) and 0 <= len(batch) < 100:
            again = with_retry(page, what=f"listings page {off} (end confirm)", max_attempts=3).json()
            more = again.get("results") if isinstance(again, dict) else again
            if isinstance(more, list) and len(more) > len(batch):
                batch = more
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100 and not onbuy.more_listings(off, 100):
            break
        off += 100
    return items


def main():
    from onbuy_client import BASE_URL, OnBuyClient
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    items = sweep(onbuy, BASE_URL)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stamps = [t for t in (parse_stamp((i or {}).get("updated_at")) for i in items) if t]
    print(f"sweep: {len(items)} listings | updated_at from {min(stamps) if stamps else '-'} to {max(stamps) if stamps else '-'} | now {now:%Y-%m-%d %H:%M:%S} UTC")
    for sku in TARGETS:
        hit = [i for i in items if str((i or {}).get("sku") or "").strip() == sku]
        for i in hit:
            print(f"TARGET {sku}: stock {i.get('stock')} price {i.get('price')} updated_at {i.get('updated_at')} created_at {i.get('created_at')}")
        if not hit:
            print(f"TARGET {sku}: no such listing")
    table = buckets(items, now)
    print(f"\nlistings last touched, per {BUCKET_MIN}-minute bucket (UTC), last {HOURS:g} h - buckets of {MIN_SHOW}+ shown: time | touched | stock 0 | stock above 0")
    for key in sorted(table):
        n, z, s = table[key]
        if n >= MIN_SHOW:
            print(f"   {key:%m-%d %H:%M} | {n:5d} | {z:5d} | {s:5d}")
    quiet = sum(v[0] for k, v in table.items() if v[0] < MIN_SHOW)
    print(f"   (smaller buckets together: {quiet} listings)")
    if WINDOW_FROM and WINDOW_TO:
        lo, hi = parse_stamp(WINDOW_FROM), parse_stamp(WINDOW_TO)
        inside = [i for i in items if (t := parse_stamp(i.get("updated_at"))) and lo <= t <= hi and to_int(i.get("stock")) > 0]
        inside.sort(key=lambda i: str(i.get("updated_at")))
        print(f"\nIN-STOCK listings last touched {WINDOW_FROM} .. {WINDOW_TO}: {len(inside)}")
        for i in inside[:SHOW_MAX]:
            print(f"   {i.get('sku')} | stock {i.get('stock')} | price {i.get('price')} | updated_at {i.get('updated_at')}")


if __name__ == "__main__":
    main()
