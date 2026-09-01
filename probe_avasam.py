"""READ-ONLY Avasam probe (2026-09-01 evaluation). Writes NOTHING - not to
Avasam, not to the Sheet, not to OnBuy. It answers the questions the docs
leave open before any integration work is committed:

  1. which token transport the data calls actually accept;
  2. which fields really come back (the union of keys, so UNDOCUMENTED ones
     - especially any per-product delivery/shipping cost - surface);
  3. how many BarCodes pass OUR GS1 check-digit rule (SKU digits ARE the
     product code here, so this decides whether products can list at all);
  4. whether `Price` is ex- or inc-VAT, tested against PriceIncVat and
     VATPercentage rather than assumed;
  5. what a real cost turns into under our own pricing bands;
  6. whether the response carries any rate-limit headers (the docs mention
     a limit twice and never quantify it).
"""
import ast
import json
import os
import re
from pathlib import Path

from avasam_client import AvasamClient

LIMIT = int(os.getenv("PROBE_LIMIT") or "10")
SHOW = int(os.getenv("PROBE_SHOW") or "3")

# Reuse the live barcode + pricing rules without importing generate_xml
# (which needs gspread/env). Same ast-extraction trick as tests/.
_SRC = Path(__file__).resolve().parent / "generate_xml.py"
_tree = ast.parse(_SRC.read_text(encoding="utf-8"))
_ns = {"re": re}
exec(compile(ast.Module(body=[n for n in _tree.body
                              if isinstance(n, ast.FunctionDef)
                              and n.name in ("is_valid_gtin", "sku_numeric_part")],
                        type_ignores=[]), str(_SRC), "exec"), _ns)
is_valid_gtin, sku_numeric_part = _ns["is_valid_gtin"], _ns["sku_numeric_part"]

import pricing  # noqa: E402

SHIP_WORDS = ("ship", "deliver", "postage", "carriage", "freight")


def rows_of(payload):
    """Endpoints return either a bare list or {"data": [...], "total": n}."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        return payload.get("data") or payload.get("Data") or [], payload.get("total")
    return [], None


def get(row, *names, default=None):
    low = {str(k).lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return default


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def report(label, rows, total):
    print(f"\n=== {label}: {len(rows)} row(s)" + (f" of {total} total" if total else ""))
    if not rows:
        print("   (empty - nothing to analyse)")
        return
    keys = sorted({k for r in rows if isinstance(r, dict) for k in r})
    print(f"   fields returned ({len(keys)}): {', '.join(keys)}")
    ship = [k for k in keys if any(w in k.lower() for w in SHIP_WORDS)]
    print(f"   >> delivery/shipping fields: {', '.join(ship) if ship else 'NONE FOUND'}")

    gtin_ok = gtin_bad = no_code = 0
    bad_examples = []
    for r in rows:
        code = sku_numeric_part(get(r, "BarCode", "Barcode", "EAN", "GTIN", default="") or "")
        if not code:
            no_code += 1
        elif is_valid_gtin(code):
            gtin_ok += 1
        else:
            gtin_bad += 1
            if len(bad_examples) < 5:
                bad_examples.append(f"{get(r, 'SKU')}={code}")
    if gtin_ok or gtin_bad or no_code:
        print(f"   >> barcodes: {gtin_ok} valid GTIN | {gtin_bad} invalid | {no_code} missing")
        if bad_examples:
            print(f"      invalid examples: {'; '.join(bad_examples)}")

    # ex-VAT vs inc-VAT, decided by arithmetic rather than assumption
    verdicts = []
    for r in rows:
        p, inc, vat = (fnum(get(r, "Price")), fnum(get(r, "PriceIncVat")),
                       fnum(get(r, "VATPercentage", "Vat")))
        if p and inc and vat is not None:
            if abs(p * (1 + vat / 100) - inc) < 0.02:
                verdicts.append("Price is EX-VAT")
            elif abs(p - inc) < 0.02:
                verdicts.append("Price already INCLUDES VAT")
            else:
                verdicts.append("unclear")
    if verdicts:
        top = max(set(verdicts), key=verdicts.count)
        print(f"   >> VAT check ({len(verdicts)} comparable rows): {top}")

    stocked = [r for r in rows if get(r, "Stock") is not None]
    if stocked:
        vals = [fnum(get(r, "Stock")) or 0 for r in stocked]
        print(f"   >> stock present on {len(stocked)}/{len(rows)}; "
              f"in-stock {sum(1 for v in vals if v > 0)}, zero {sum(1 for v in vals if v <= 0)}")

    have = lambda *n: sum(1 for r in rows if str(get(r, *n) or "").strip())  # noqa: E731
    print(f"   >> content: title {have('Title')}/{len(rows)}, "
          f"description {have('Description', 'description')}/{len(rows)}, "
          f"image {have('Image', 'image', 'ProductImage')}/{len(rows)}, "
          f"category {have('Category')}/{len(rows)}")

    print(f"   -- first {min(SHOW, len(rows))} row(s), pricing under OUR bands:")
    for r in rows[:SHOW]:
        cost = fnum(get(r, "Price")) or 0
        sell = pricing.calculate_selling_price(cost) if cost > 0 else 0
        print(f"      SKU {get(r, 'SKU')!r} | cost {cost} -> our sell {sell} "
              f"| stock {get(r, 'Stock')} | {str(get(r, 'Title'))[:60]!r}")


def main():
    c = AvasamClient()
    print(f"base: {os.getenv('AVASAM_BASE_URL') or 'https://app.avasam.com'} | limit {LIMIT}")
    c.authenticate()
    print(f"AUTH OK - token acquired, expires_at={c.expires_at}")
    print(f"auth response fields: {c.auth_response_keys or '(none)'}"
          f" | customerId: {c.client_id or 'none'} (quote this to Avasam support)"
          f" | EndPoint: {c.end_point or 'not returned'}")

    for label, fn in (("GetSellerProductList", lambda: c.get_seller_product_list(0, LIMIT)),
                      ("GetInventoryListWithFilter", lambda: c.get_inventory_with_filter(0, LIMIT)),
                      ("SellerStockList", lambda: c.seller_stock_list(0, LIMIT))):
        try:
            rows, total = rows_of(fn())
            report(label, rows, total)
            print(f"   auth transport accepted: {c.auth_style}")
            if c.last_rate_headers:
                print(f"   rate-limit headers: {json.dumps(c.last_rate_headers)}")
        except Exception as exc:
            print(f"\n=== {label}: FAILED - {type(exc).__name__}: {str(exc)[:300]}")
            if c.attempts:
                print("   every transport tried (label | status | body | WWW-Authenticate):")
                for lbl, status, snippet, hint in c.attempts:
                    print(f"      {lbl:38s} | {status} | {snippet or chr(40) + chr(41)} | {hint or chr(45)}")
                c.attempts = []

    print("\nrate-limit headers seen on the last call: "
          f"{json.dumps(c.last_rate_headers) if c.last_rate_headers else 'NONE (ask Avasam for the numbers)'}")
    print("read-only probe complete - nothing was written anywhere.")


if __name__ == "__main__":
    main()
