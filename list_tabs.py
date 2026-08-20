"""One-off, READ-ONLY: list the spreadsheet's tabs in ORDER with row-1
headers - the pipeline reads .sheet1 (the tab at index 0), so an import
landing in first position would silently become the pipeline's data."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("OnBuy_Feed_Master")
    for i, w in enumerate(ss.worksheets()):
        hdr = [str(h).strip() for h in w.row_values(1)][:6]
        print(f"TAB {i}: '{w.title}' rows={w.row_count} first-headers={hdr}")


if __name__ == "__main__":
    main()
