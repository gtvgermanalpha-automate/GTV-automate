"""READ-ONLY (2026-11-05): show how each zero-stock safety layer sees named SKUs on the CURRENT sheet.

Built for the GTV order T6R9KFW case (SKU 175321526932): OnBuy's own API log shows that NO stock-0 push reached the listing between
2026-11-03 and 2026-11-04, although the oversell guard, the OOS pass and the audit all ran. This runs the very selection code of each
layer on the live sheet and prints, per SKU, whether the layer would push it and which rule drops it. It makes NO OnBuy call and writes
nothing anywhere. Env: SKUS (comma-separated, exact), SHEET_NAME.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import audit_price_drift
import oversell_guard
import sheet_tabs
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
WANT = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]


def parse_time(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(2000, 1, 1)


def guard_reason(r, seen):
    """The guard's rule chain (oversell_guard.candidates) for ONE row, as text."""
    if not r["sku"]:
        return "no SKU"
    if r["stock"] != 0:
        return f"stock is {r['stock']!r}, not 0"
    sync = str(r.get("sync") or "").strip()
    frozen_live = sync.startswith(oversell_guard.FROZEN_PREFIXES) and r["created"]
    if not (sync.startswith(oversell_guard.ELIGIBLE_PREFIXES) or frozen_live):
        return f"Sync Status {sync[:50]!r} is not a live status"
    status = str(r.get("status") or "").strip().upper()
    if status and status != "INACTIVE":
        return f"Status {status!r} disagrees with stock 0"
    if seen.get(r["sku"], 0) > 1:
        return f"SKU is on {seen[r['sku']]} sheet rows"
    price = r.get("price") or 0.0
    if price <= 0:
        return "no price"
    if price < oversell_guard.MIN_PRICE:
        return f"price {price} under the minimum"
    return "PUSHED"


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)

    # 1. the oversell guard: its own reader and its own selection
    rows = oversell_guard.read_rows(book)
    pushes, cs = oversell_guard.candidates(rows)
    pos = {s: i for i, (s, _p) in enumerate(pushes)}
    seen = {}
    for r in rows:
        seen[r["sku"]] = seen.get(r["sku"], 0) + 1
    print(f"GUARD whole pass now: {cs} | {len(pushes)} SKUs would be pushed")
    for s in WANT:
        mine = [r for r in rows if r["sku"] == s]
        print(f"GUARD: SKU {s} is on {len(mine)} row(s) the guard reads")
        for r in mine:
            print(f"   {r['tab']} row {r['row']}: stock={r['stock']!r} status={r['status']!r} sync={r['sync'][:40]!r} created={r['created']} price={r['price']} -> {guard_reason(r, seen)}")
        print(f"GUARD: would push {s}: {s in pos}" + (f" (index {pos[s]} of {len(pushes)})" if s in pos else ""))

    # 2. the sync's start-of-run OOS pass (generate_xml.py ~1803-1840), tab by tab
    tabs = [sheet_tabs.product_sheet(book)]
    try:
        amz = book.worksheet(sheet_tabs.AMAZON_TAB)
        if amz.title != tabs[0].title:
            tabs.append(amz)
    except gspread.exceptions.WorksheetNotFound:
        pass
    for ws in tabs:
        data = with_retry(ws.get_all_records, what=f"read {ws.title}", max_attempts=3)
        pending = 0
        for row in data:
            status = str(row.get("Sync Status") or "").strip()
            frozen_live = (status.startswith(("Failed", "BRAND BLOCKED"))
                           and str(row.get("OnBuy Product Created") or "").strip().upper() == "TRUE")
            if not (status.startswith(("Synced", "Pending Approval", "Awaiting OnBuy go-live")) or frozen_live):
                continue
            raw = str(row.get("Stock") if row.get("Stock") is not None else "").strip()
            try:
                if raw == "" or int(float(raw)) != 0:
                    continue
            except (TypeError, ValueError):
                continue
            if parse_time(row.get("Last OnBuy Sync", "")) >= parse_time(row.get("Last Checked Time", "")):
                continue
            if str(row.get("SKU") or "").strip():
                pending += 1
        print(f"OOS PASS tab {ws.title}: {pending} row(s) would be selected right now (sheet-0, live status, last push older than last check)")
        # how long rows wait between two supplier checks = the window in which a sold-out supplier item still shows stock
        now_pkt = datetime.now(timezone(timedelta(hours=5))).replace(tzinfo=None)
        ages = []
        for row in data:
            if not str(row.get("SKU") or "").strip() or not str(row.get("Sync Status") or "").strip().startswith("Synced"):
                continue
            t = parse_time(str(row.get("Last Checked Time") or ""))
            if t.year >= 2020:
                ages.append((now_pkt - t).total_seconds() / 3600.0)
        if ages:
            ages.sort()
            def _q(p):
                return ages[min(len(ages) - 1, int(p * len(ages)))]
            edges = [(0, 6), (6, 12), (12, 24), (24, 36), (36, 48), (48, 10 ** 6)]
            spread = " ".join(f"{lo}-{hi if hi < 10 ** 6 else 'more'}h:{sum(1 for a in ages if lo <= a < hi)}" for lo, hi in edges)
            print(f"CHECK AGE tab {ws.title}: {len(ages)} Synced rows | median {_q(.5):.1f} h | p90 {_q(.9):.1f} h | max {ages[-1]:.1f} h | {spread}")
        for idx, row in enumerate(data):
            sku = str(row.get("SKU") or "").replace(",", "").strip()
            if sku in WANT:
                status = str(row.get("Sync Status") or "").strip()
                raw = row.get("Stock")
                los, lct = str(row.get("Last OnBuy Sync", "")), str(row.get("Last Checked Time", ""))
                print(f"OOS PASS: {ws.title} row {idx + 2} SKU {sku}: Sync Status={status[:40]!r} Stock={raw!r} LastOnBuySync={los!r} "
                      f"LastChecked={lct!r} stale={parse_time(los) < parse_time(lct)} price={row.get('Selling Price (£)')!r}")

    # 3. the nightly audit's reading of the sheet (audit_price_drift.sheet_rows: first tab wins on a repeated SKU)
    sheet, defended = audit_price_drift.sheet_rows()
    print(f"AUDIT sheet map: {len(sheet)} SKUs | BuyBox-defended: {len(defended)}")
    for s in WANT:
        print(f"AUDIT: sheet view of {s}: {sheet.get(s)} | BuyBox-defended: {s in defended}")


if __name__ == "__main__":
    main()
