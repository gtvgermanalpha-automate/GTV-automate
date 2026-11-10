"""One-off: blank 'Last OnBuy Sync' on the 63 re-SKU'd Christmas-tree rows (Amazon tab).

generate_xml.py hands the capped OnBuy push slots to rows whose 'Last OnBuy Sync' is blank
first (blank parses to year 2000), then oldest-pushed. These 63 rows still carry the stamp
2026-11-06 03:43 from their OLD spare SKUs, so ~195 price updates out-rank them every run and
they have sat uncreated since 11-06. Blanking the stamp makes them win the first slots of the
next scheduled Amazon run. Touches ONLY rows whose SKU is in the embedded list AND whose OPC
and Sync Status cells are still empty (the create-path state) - anything else is skipped loud.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

DRY = (os.getenv("DRY_RUN") or "yes").strip().lower() != "no"
SHEET_NAME = os.getenv("SHEET_NAME") or "OnBuy_Feed_Master"
SKUS = {
    "837760098981",
    "837773092211",
    "837806213675",
    "837810876309",
    "837827127951",
    "837860101482",
    "837940595965",
    "837946905553",
    "837949009050",
    "837956835994",
    "837961479596",
    "837969527220",
    "837979906053",
    "837989024129",
    "838039952843",
    "838048467710",
    "838064539613",
    "838102818397",
    "838112311963",
    "838112794971",
    "838114497603",
    "838132051764",
    "838132212684",
    "838132874530",
    "838134827176",
    "838143005794",
    "838151512215",
    "838165495153",
    "838174205460",
    "838181753039",
    "838182130327",
    "838207040907",
    "838217368275",
    "838227261795",
    "838256706519",
    "838268700031",
    "838331810681",
    "838350363014",
    "838447017370",
    "838470845773",
    "838476491967",
    "838517309602",
    "838517710637",
    "838529770858",
    "838535868983",
    "838576921999",
    "838577197836",
    "838580588393",
    "838609815851",
    "838623884833",
    "838636410555",
    "838645995432",
    "838667657110",
    "838680952193",
    "838685345426",
    "838701862098",
    "838702461764",
    "838725379398",
    "838778044403",
    "838785907197",
    "838789994681",
    "838796663570",
    "838807720179"
}


def col_letter(idx0):
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
ws = gspread.authorize(creds).open(SHEET_NAME).worksheet("Amazon")
vals = ws.get_all_values()
hdr = [h.strip() for h in vals[0]]
col = {name: hdr.index(name) for name in ("SKU", "OPC", "Sync Status", "Last OnBuy Sync")}
hits, skips, updates = [], [], []
for i, row in enumerate(vals[1:], start=2):
    def cell(name):
        j = col[name]
        return (row[j] if j < len(row) else "").strip()
    sku = cell("SKU")
    if sku not in SKUS:
        continue
    if cell("OPC") or cell("Sync Status"):
        skips.append(f"row {i} {sku}: OPC={cell('OPC')!r} Sync Status={cell('Sync Status')!r} - NOT create-path, skipped")
        continue
    hits.append(f"row {i} {sku}: stamp {cell('Last OnBuy Sync')!r} -> ''")
    updates.append({"range": f"{col_letter(col['Last OnBuy Sync'])}{i}", "values": [[""]]})
print(f"matched {len(hits)} of {len(SKUS)} SKUs | skipped {len(skips)} | {'DRY RUN' if DRY else 'LIVE'}")
for ln in hits:
    print(" ", ln)
for ln in skips:
    print(" !", ln)
missing = len(SKUS) - len(hits) - len(skips)
if missing:
    print(f"WARNING: {missing} SKU(s) not found on the Amazon tab at all")
if not DRY and updates:
    ws.batch_update(updates)
    print(f"WROTE {len(updates)} blank stamp cell(s)")
