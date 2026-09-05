"""READ-ONLY: dump a row range WITH the team's highlight colour, so a
"look at the rows I marked" request can be answered against the exact
rows, and score each description for the seller junk the sanitizer is
supposed to remove - prices, shipping/returns policy, store menus and
links, feedback pleas, cross-sell blocks. Also reports the image URLs
so a "pictures never load on OnBuy" row can be checked in the same pass.

Writes dump_rows.csv. Touches nothing.
"""
import csv
import json
import os
import re

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
ROWS = os.getenv("ROWS") or ""
OUT = "dump_rows.csv"

JUNK = [
    ("price", re.compile(r"(£|GBP|\$)\s?\d|\d\s?(£|GBP)", re.I)),
    ("shipping/returns", re.compile(r"\b(shipping|postage|dispatch(ed)?|deliver(y|ed)|returns?|refund)\b", re.I)),
    ("store/menu", re.compile(r"\b(our (ebay )?store|store (home|categories|menu)|visit (our|us)|shop (now|categories)|about us|contact us|feedback|payment|terms)\b", re.I)),
    ("link", re.compile(r"(https?://|www\.|<a\s|href=)", re.I)),
    ("ebay word", re.compile(r"\bebay\b", re.I)),
    ("cross-sell", re.compile(r"\b(you may (also )?like|similar (items|products)|related (items|products)|customers also|see also|more items)\b", re.I)),
    ("branding/seller", re.compile(r"\b(powered by|frooition|inkfrog|crazylister|template by|©|copyright|all rights reserved)\b", re.I)),
]


def parse_rows(spec):
    out = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def rgb_hex(bg):
    if not bg:
        return ""
    r = int(round(bg.get("red", 1) * 255)); g = int(round(bg.get("green", 1) * 255)); b = int(round(bg.get("blue", 1) * 255))
    if (r, g, b) == (255, 255, 255):
        return ""
    return f"#{r:02x}{g:02x}{b:02x}"


def colour_name(h):
    if not h:
        return ""
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    if g > r + 40 and g > b + 40:
        return "green"
    if r > g + 40 and r > b + 40:
        return "red"
    if r > 200 and g > 200 and b < 150:
        return "yellow"
    if r > 200 and 120 < g < 200 and b < 120:
        return "orange/amber"
    return "other"


def main():
    rows_wanted = parse_rows(ROWS)
    if not rows_wanted:
        raise SystemExit("ROWS required")
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    ss = client.open(SHEET_NAME)
    sheet = ss.sheet1
    values = sheet.get_all_values()
    headers = [h.strip() for h in values[0]]
    idx = {h.lower(): i for i, h in enumerate(headers)}

    def cell(row, name):
        i = idx.get(name.lower())
        return (row[i] if i is not None and i < len(row) else "").strip()

    # background colour of column A for each wanted row (one metadata call)
    lo, hi = min(rows_wanted), max(rows_wanted)
    meta = ss.fetch_sheet_metadata(params={"includeGridData": True,
                                           "ranges": [f"{sheet.title}!A{lo}:A{hi}"],
                                           "fields": "sheets.data.rowData.values.effectiveFormat.backgroundColor"})
    colours = {}
    try:
        rowdata = meta["sheets"][0]["data"][0].get("rowData", [])
        for off, rd in enumerate(rowdata):
            vals = rd.get("values") or [{}]
            bg = (vals[0].get("effectiveFormat") or {}).get("backgroundColor")
            colours[lo + off] = rgb_hex(bg)
    except (KeyError, IndexError):
        pass

    out_rows = []
    for r in rows_wanted:
        if r - 1 >= len(values):
            continue
        row = values[r - 1]
        desc = cell(row, "Description")
        text = re.sub(r"<[^>]+>", " ", desc)
        hits = []
        for label, rx in JUNK:
            m = rx.search(text if label != "link" else desc)
            if m:
                s = max(0, m.start() - 40); e = min(len(text if label != "link" else desc), m.end() + 60)
                snippet = re.sub(r"\s+", " ", (text if label != "link" else desc)[s:e]).strip()
                hits.append(f"{label}: …{snippet}…")
        imgs = [u.strip() for u in cell(row, "Additional Images").split(",") if u.strip()]
        main_img = cell(row, "Image URL")
        col = colours.get(r, "")
        out_rows.append({
            "row": r, "colour": colour_name(col) or col, "sku": cell(row, "SKU"),
            "status": cell(row, "Sync Status")[:60], "opc": cell(row, "OPC"),
            "checked": cell(row, "Last Checked Time"),
            "title": cell(row, "Title")[:70],
            "desc_len": len(desc), "desc_html_tags": len(re.findall(r"<[^>]+>", desc)),
            "junk_hits": len(hits), "junk_detail": " || ".join(hits)[:900],
            "main_image": main_img, "extra_images": len(imgs),
            "image_hosts": ",".join(sorted({re.sub(r"^https?://([^/]+).*$", r"\1", u) for u in [main_img] + imgs if u})),
            "image_exts": ",".join(sorted({(u.rsplit(".", 1)[-1][:5].lower() if "." in u.rsplit("/", 1)[-1] else "?") for u in [main_img] + imgs if u})),
        })

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    print(f"rows dumped: {len(out_rows)} -> {OUT}")
    for o in out_rows:
        print(f"ROW {o['row']} [{o['colour'] or '-':>7}] {o['sku']:<14} {o['status'][:34]:<34} imgs={1 if o['main_image'] else 0}+{o['extra_images']} {o['image_exts']:<9} desc={o['desc_len']:>5} junk={o['junk_hits']}")
        if o["junk_detail"]:
            print(f"        {o['junk_detail'][:300]}")
        if o["colour"] == "red":
            print(f"        main image: {o['main_image'][:120]}")


if __name__ == "__main__":
    main()
