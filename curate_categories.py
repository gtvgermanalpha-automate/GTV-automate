"""One-off: hand-curated category corrections from the 2026-08-10
correctness scan (tier-2 review). Every SKU below was checked by eye
against its title; writes ONLY the Category cell - Sync Status and
OnBuy state stay untouched. DRY_RUN default on."""

SHEET_NAME = 'OnBuy_Feed_Master'

CORRECTIONS = {
    '167297714604': 'Home & Garden > Kitchen & Home Appliances > Appliance Parts & Accessories > Vacuum Cleaner Parts & Accessories',
    '184520000983': 'Home & Garden > Kitchen & Home Appliances > Appliance Parts & Accessories > Vacuum Cleaner Parts & Accessories',
    '703932501934': 'Cars & Automotive > Motorbike Parts & Accessories > Motorbike Protective Clothing > Motorbike Helmet Parts & Accessories',
    '708032447489': 'Home & Garden > Garden & Outdoor Living > Outdoor Power Tool Accessories > Strimmer Parts & Accessories',
    '116826862620': 'Health & Beauty > Skin Care > Beauty Face Masks > Sleep Masks',
    '254152116311': 'Home & Garden > Cooking, Dining & Barware > Food & Drink Carriers > Cool Bags & Cool Boxes',
    '194343045337': 'Home & Garden > Kitchen & Home Appliances > Appliance Parts & Accessories > Microwave Parts & Accessories',
    '873036890866': 'Sports & Outdoors > Cycling > Bike Accessories > Bike Locks',
    '404834092420': "Clothing, Shoes & Accessories > Women's Clothing > Women's Activewear > Women's Swimwear",
    '404834097593': 'Home & Garden > Laundry, Cleaning & Storage > Laundry Supplies > Washing Lines',
    '468883382161': 'Home & Garden > Garden & Outdoor Living > Garden Decor > Garden Ornaments & Sculptures',
    '948645979773': 'Electronics & Technology > Computing & Gaming > Computer Monitors & Monitor Accessories > Computer Monitors',
    '869740730338': "Toys & Games > Toys > Children's Scooters & Ride-On Toys > Electric Ride Ons",
    '869970407628': "Toys & Games > Toys > Children's Scooters & Ride-On Toys > Electric Ride Ons",
    '869612277534': "Toys & Games > Toys > Children's Scooters & Ride-On Toys > Electric Ride Ons",
    '851056663295': 'Home & Garden > Kitchen & Home Appliances > Climate Control Appliances > Humidifiers',
    '624707759162': 'Electronics & Technology > Cables & Adapters > Adapters > DVI & HDMI Adapters',
    '194343045429': 'Clothing, Shoes & Accessories > Luggage, Bags & Travel Accessories > Bags & Backpacks > Backpacks',
    '990224414304': 'Home & Garden > Garden & Outdoor Living > Outdoor Power Tool Accessories > Pressure Washer Parts & Accessories',
    '869504708641': 'Home & Garden > Furniture, Furnishings & Decor > Lighting > Light Bulbs',
    '194343045641': 'Cars & Automotive > Vehicle Care & Maintenance > Vehicle Care & Cleaning > Car Brushes, Cloths & Sponges',
    '194343045665': 'Clothing, Shoes & Accessories > Shoes > Shoe Accessories > Shoe Insoles & Inserts',
    '194343045672': 'Clothing, Shoes & Accessories > Shoes > Shoe Accessories > Shoe Insoles & Inserts',
}

import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")


def col_letter(n):
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def main():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]), scope)
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {col: idx + 1 for idx, col in enumerate(headers) if col}
    data = sheet.get_all_records()

    updates, planned = [], []
    for idx, row in enumerate(data, start=2):
        sku = str(row.get("SKU") or "").strip()
        want = CORRECTIONS.get(sku)
        if not want:
            continue
        cat = str(row.get("Category") or "").strip()
        if cat.lower() == want.lower():
            continue
        planned.append((idx, sku, cat, want))
        updates.append({"range": f"{col_letter(col_map['Category'])}{idx}", "values": [[want]]})

    for idx, sku, cat, want in planned:
        print(f"row {idx} {sku}")
        print(f"    {cat or '(blank)'}  ->  {want}")
    print(f"\ncorrections planned: {len(planned)}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    if updates:
        sheet.batch_update(updates)
        print(f"Written {len(updates)} Category cell(s).")


if __name__ == "__main__":
    main()
