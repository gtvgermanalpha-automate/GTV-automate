"""Refresh onbuy_categories_only.csv from OnBuy's live category tree.

2026-08-21: OnBuy rejected creates with "Category '3472' is not a lowest
level category" (Ceiling Lights) and the same for 13705 (Lamps) - the tree
had grown child categories our CSV never knew about. GET /v2/categories
exposes every node with a can_list_in flag, so the listable set can be
rebuilt from source instead of guessed.

Writes onbuy_categories_only.csv (Category ID, OnBuy Category Path) with
ONLY can_list_in=true nodes, path = category_tree + " > " + name, sorted by
path. Prints a diff against the previous file: IDs that stopped being
listable (rows carrying those need remapping) and new listable IDs.
DRY_RUN=1 (default) prints the diff and writes the new file as
onbuy_categories_only.new.csv instead."""
import csv
import io
import os
import time

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "onbuy_categories_only.csv")


def fetch_all(onbuy):
    out, offset, limit = [], 0, 100
    while True:
        def _page(off=offset):
            resp = onbuy._send("GET", f"{BASE_URL}/categories", what="categories page",
                               params={"site_id": onbuy.site_id, "limit": limit, "offset": off},
                               timeout=60)
            resp.raise_for_status()
            return resp
        body = with_retry(_page, what=f"categories page {offset}", max_attempts=3).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            break
        out.extend(items)
        if len(items) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return out


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    nodes = fetch_all(onbuy)
    print(f"categories fetched: {len(nodes)}")
    # category_tree is blank on ~700 nodes in the API response, so build
    # every path from the parent chain instead (name of each ancestor,
    # top-level first) - the matcher scores on path tokens, so paths must
    # stay complete and identical in shape to the old file.
    by_id = {str(n.get("category_id")): n for n in nodes if n.get("category_id") is not None}
    memo = {}

    def path_of(cid, depth=0):
        if cid in memo:
            return memo[cid]
        n = by_id.get(cid)
        if not n or depth > 12:
            return ""
        name = str(n.get("name") or "").strip()
        parent = str(n.get("parent_id") or "").strip()
        up = path_of(parent, depth + 1) if parent and parent in by_id else ""
        memo[cid] = f"{up} > {name}" if up else name
        return memo[cid]

    listable = {}
    blank_tree = 0
    for n in nodes:
        if not n.get("can_list_in"):
            continue
        cid = str(n.get("category_id") or "").strip()
        if not str(n.get("category_tree") or "").strip():
            blank_tree += 1
        path = path_of(cid)
        if cid and path:
            listable[cid] = path
    print(f"nodes with blank category_tree in API: {blank_tree} (paths rebuilt from parent chain)")
    print(f"listable (can_list_in=true): {len(listable)}")

    old = {}
    if os.path.exists(CSV_PATH):
        with io.open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                old[str(r.get("Category ID") or "").strip()] = str(r.get("OnBuy Category Path") or "").strip()
    gone = {k: v for k, v in old.items() if k not in listable}
    new_ids = {k: v for k, v in listable.items() if k not in old}
    renamed = {k: (old[k], listable[k]) for k in listable if k in old and old[k] != listable[k]}
    print(f"previous file: {len(old)} | no longer listable: {len(gone)} | newly listable: {len(new_ids)} | path changed: {len(renamed)}")
    for k, v in list(gone.items())[:40]:
        print(f"  GONE {k}: {v}")
    for k, v in list(new_ids.items())[:40]:
        print(f"  NEW  {k}: {v}")
    for k, (a, b) in list(renamed.items())[:20]:
        print(f"  RENAMED {k}: {a}  ->  {b}")

    target = CSV_PATH if not DRY_RUN else CSV_PATH.replace(".csv", ".new.csv")
    with io.open(target, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Category ID", "OnBuy Category Path"])
        for cid, path in sorted(listable.items(), key=lambda kv: kv[1]):
            w.writerow([cid, path])
    print(f"written: {target} ({len(listable)} rows){' [DRY RUN copy]' if DRY_RUN else ''}")


if __name__ == "__main__":
    main()
