"""One-off: apply HAND-CURATED categories for rows the strict matcher
refused. The relaxed auto-scorer tried first and produced DisplayPort-
grade mistakes (Smart TV -> TV Smart Glasses), so each entry below was
chosen by a human eye against the official category file (2026-08-01).
Rows not in the map stay on the employee worklist. DRY_RUN honoured.
"""
import json
import logging
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("onbuy_sync")

SHEET_NAME = "OnBuy_Feed_Master"
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")

CURATED = {
    "389831061399": "Electronics & Technology > TV & Audio > TVs & Accessories > TV Aerials",
    "428327259640": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "429548944339": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "434096992794": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "458779866677": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "478773988816": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "670314034253": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "690106929116": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "708130585342": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "489065092418": "Electronics & Technology > TV & Audio > TVs & Accessories > TVs",
    "644368863442": "Electronics & Technology > Computing & Gaming > Laptops, MacBooks & Accessories > Laptops",
    "669246611540": "Electronics & Technology > Mobile & Smart Tech > Mobile Phones & Accessories > Mobile Phones",
    "662419262662": "Electronics & Technology > Mobile & Smart Tech > Mobile Phones & Accessories > Mobile Phones",
    "669342643490": "Electronics & Technology > Mobile & Smart Tech > Mobile Phones & Accessories > Mobile Phones",
    "671076171408": "Electronics & Technology > Computing & Gaming > Computer Monitors & Monitor Accessories > Computer Monitors",
    # 2026-08-05 batch - diagnosed with diagnose_categories.py: all of these
    # have NO eBay Type item-specific (Anker/TCL/Unihertz/iiyama listings
    # don't set one), so the authoritative stage never ran, and the title
    # scorer drowned in description boilerplate ("part", "replacement",
    # "accessory"...). Titles verified by eye against the category file.
    # Android tablets ("12S PRO Wifi Tablet Android 15"):
    "932920150520": "Electronics & Technology > Computing & Gaming > iPads, Tablets & eBook Readers > Tablets",
    "933317496320": "Electronics & Technology > Computing & Gaming > iPads, Tablets & eBook Readers > Tablets",
    "934451332352": "Electronics & Technology > Computing & Gaming > iPads, Tablets & eBook Readers > Tablets",
    # Phones (TCL Plex; Unihertz Jelly Star):
    "934731919556": "Electronics & Technology > Mobile & Smart Tech > Mobile Phones & Accessories > Mobile Phones",
    "932310817255": "Electronics & Technology > Mobile & Smart Tech > Mobile Phones & Accessories > Mobile Phones",
    # soundcore earbuds (Liberty Buds x2, P31i, P30i, Liberty 4 Pro) - the
    # category file has no separate earbuds leaf; Headphones is the device
    # leaf for the whole subtree:
    "940665548243": "Electronics & Technology > TV & Audio > Headphones & Accessories > Headphones",
    "941782726040": "Electronics & Technology > TV & Audio > Headphones & Accessories > Headphones",
    "941726818060": "Electronics & Technology > TV & Audio > Headphones & Accessories > Headphones",
    "943585322337": "Electronics & Technology > TV & Audio > Headphones & Accessories > Headphones",
    "942259390658": "Electronics & Technology > TV & Audio > Headphones & Accessories > Headphones",
    # soundcore Bluetooth speakers (Boom Go 3i x3, Boom 2 Plus):
    "940730493072": "Electronics & Technology > TV & Audio > Speakers & Sound Systems > Speakers",
    "940732984264": "Electronics & Technology > TV & Audio > Speakers & Sound Systems > Speakers",
    "940769330201": "Electronics & Technology > TV & Audio > Speakers & Sound Systems > Speakers",
    "943353271096": "Electronics & Technology > TV & Audio > Speakers & Sound Systems > Speakers",
    # iiyama GB2771QSU 27" gaming monitor:
    "946215035461": "Electronics & Technology > Computing & Gaming > Computer Monitors & Monitor Accessories > Computer Monitors",
    # eufy Entry Sensor E20 (door/window security sensor - closest leaf):
    "942947672073": "Tools & DIY > Home Safety & Security > Alarms & Detectors > Motion Sensors",
}


def main():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]), scope)
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    data = sheet.get_all_records()
    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {col: idx + 1 for idx, col in enumerate(headers) if col}

    def col_letter(n):
        out = ""
        while n:
            n, rem = divmod(n - 1, 26)
            out = chr(65 + rem) + out
        return out

    updates, applied = [], []
    for idx, row in enumerate(data, start=2):
        sku = str(row.get("SKU") or "").strip()
        if sku in CURATED and "no matching OnBuy category" in str(row.get("Sync Status") or ""):
            path = CURATED[sku]
            applied.append((idx, sku, path))
            updates.append({"range": f"{col_letter(col_map['Category'])}{idx}", "values": [[path]]})
            updates.append({"range": f"{col_letter(col_map['Sync Status'])}{idx}", "values": [[""]]})
    for idx, sku, path in applied:
        logger.info("row %d %s -> %s", idx, sku, path)
    logger.info("curated categories to apply: %d", len(applied))
    if DRY_RUN:
        logger.info("DRY RUN - nothing written")
        return
    if updates:
        sheet.batch_update(updates)
        logger.info("Written - these rows retry on the next scheduled run")


if __name__ == "__main__":
    main()
