"""READ-ONLY (2026-11-06): compare one tab in two Drive revisions BY SKU (not by row position).

restore_sku_column's `diff` compares rows by position, so one deleted row makes every row below look changed. This lists which
SKUs left the tab, which arrived, which rows were blanked, and how far the rest moved - i.e. what an edit session really did.
Prints no cost cells. Writes nothing. Env: REVISION, REVISION2 (Drive revision ids from restore_sku_column action=list), TAB.
"""
import collections
import os

import restore_sku_column as rsc

SHOW = ("Title", "Stock", "Sync Status", "OPC", "OnBuy Product Created")


def snapshot(creds, rev, tab):
    header, rows, _ = rsc.old_tab(creds, rev, tab)
    names = [rsc.norm(h) for h in header]
    ix = {h: j for j, h in enumerate(names) if h}
    out, blank = {}, []
    for n, r in enumerate(rows, start=2):
        sku = rsc.sku_text(rsc.sku_cell(rsc.cell(r, ix["SKU"]))) if "SKU" in ix else ""
        sku = str(sku or "").replace(",", "").strip()
        url = rsc.norm(rsc.cell(r, ix["Supplier URL"])) if "Supplier URL" in ix else ""
        if not sku and not url:
            blank.append(n)
            continue
        cells = {c: rsc.norm(rsc.cell(r, ix[c]))[:60] for c in SHOW if c in ix}
        cells["has_url"] = bool(url)
        out.setdefault(sku or f"(no SKU, row {n})", []).append((n, cells))
    return out, blank, len(rows)


def main():
    a_id, b_id = os.environ["REVISION"].strip(), os.environ["REVISION2"].strip()
    tab = (os.getenv("TAB") or "Amazon").strip()
    creds, gc = rsc._clients()
    book = gc.open(rsc.SHEET_NAME)
    revs = {r["id"]: r for r in rsc.list_revisions(creds, book.id)}
    for rid in (a_id, b_id):
        r = revs[rid]
        print(f"revision {rid}: {r['modifiedTime']} by {rsc.who(r)}")
    a, a_blank, a_n = snapshot(creds, revs[a_id], tab)
    b, b_blank, b_n = snapshot(creds, revs[b_id], tab)
    print(f"[{tab}] rows {a_n} -> {b_n} | SKUs {len(a)} -> {len(b)} | blank rows inside the data {len(a_blank)} -> {len(b_blank)}")
    gone = [s for s in a if s not in b]
    new = [s for s in b if s not in a]
    print(f"\nSKUs GONE ({len(gone)}):")
    for s in sorted(gone, key=lambda s: a[s][0][0]):
        for n, c in a[s]:
            print(f"  GONE|row {n}|{s}|url={'yes' if c['has_url'] else 'no'}|stock={c.get('Stock','')}|sync={c.get('Sync Status','')[:30]}|opc={c.get('OPC','')}|{c.get('Title','')}")
    print(f"\nSKUs NEW ({len(new)}):")
    for s in sorted(new, key=lambda s: b[s][0][0]):
        for n, c in b[s]:
            print(f"  NEW|row {n}|{s}|url={'yes' if c['has_url'] else 'no'}|{c.get('Title','')}")
    moved = collections.Counter()
    url_added = []
    for s in a:
        if s in b:
            d = b[s][0][0] - a[s][0][0]
            moved[d] += 1
            if not a[s][0][1]["has_url"] and b[s][0][1]["has_url"]:
                url_added.append((b[s][0][0], s, b[s][0][1].get("Title", "")))
    print(f"\nrow moves of the SKUs on both: {dict(sorted(moved.items()))}")
    print(f"spare SKUs that got a Supplier URL: {len(url_added)}" + (f" (rows {url_added[0][0]}..{url_added[-1][0]})" if url_added else ""))
    new_blank = sorted(set(b_blank) - set(a_blank))
    print(f"rows blank in the second revision only: {len(new_blank)} {new_blank[:40]}")


if __name__ == "__main__":
    main()
