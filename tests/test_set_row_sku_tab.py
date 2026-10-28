"""set_row_sku (2026-10-28): the row can be on the AMAZON tab, the new SKU is refused when ANY row of either product tab has it (or its digits - the product code OnBuy matches on), and the run says
whether the new SKU's digits are a valid barcode. The first typed code for Amazon row 5198 was YRA's dishwasher code: a SKU that is also a valid barcode attaches to whatever product has it."""
import sys
from pathlib import Path

import gspread
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import set_row_sku as srs

ROOT = Path(__file__).resolve().parents[1]
HEADER = ["SKU", "Title", "Sync Status", "OPC", "Last OnBuy Sync", "OnBuy Product Created", "OnBuy Listing Active", "OnBuy Product ID", "Last Checked Time"]
DISHWASHER_CODE = "761609482085"            # valid UPC-A: YRA's dishwasher product
XBOX_OLD = "693236535554"                   # valid UPC-A: the code that attached the Xbox to another product


def values(*rows):
    return [HEADER] + [list(r) + [""] * (len(HEADER) - len(r)) for r in rows]


class Sheet:
    def __init__(self, title, vals):
        self.title, self.vals, self.updates = title, vals, []

    def get_all_values(self):
        return self.vals

    def batch_update(self, updates, value_input_option=None):
        self.updates.extend(updates)


class Book:
    def __init__(self, *sheets):
        self.sheets = list(sheets)

    def worksheet(self, name):
        for s in self.sheets:
            if s.title == name:
                return s
        raise gspread.exceptions.WorksheetNotFound(name)

    def worksheets(self):
        return list(self.sheets)


def run(monkeypatch, book, **env):
    class Client:
        def open(self, name):
            return book

    monkeypatch.setenv("GOOGLE_CREDENTIALS", "{}")
    monkeypatch.setattr(srs.ServiceAccountCredentials, "from_json_keyfile_dict", staticmethod(lambda *a, **k: object()))
    monkeypatch.setattr(srs.gspread, "authorize", lambda creds: Client())
    for key, value in {"ROW": 3, "NEW_SKU": "Ama-B0H2HJ1V55-1", "EXPECT_SKU": DISHWASHER_CODE, "EXPECT_TITLE": "Xbox Series X", "DRY_RUN": False, "SHEET_TAB": "Amazon", **env}.items():
        monkeypatch.setattr(srs, key, value)
    srs.main()


def book():
    first = Sheet("Sheet1", values(["404834092055", "A kettle", "Synced", "PAAA111"], ["194343045689", "A lamp", "Synced", "PBBB222"]))
    amazon = Sheet("Amazon", values(["344677307704", "A mug", "Synced", "PCCC333"], [DISHWASHER_CODE, "Xbox Series X 1TB Black (New Packaging)", "Synced", "PY22DC9", "2026-10-28 03:18:45", "TRUE", "TRUE", "6ae123"]))
    return Book(first, amazon), first, amazon


# ---- the pure parts ---------------------------------------------------------------------------------------------------------------------
def test_which_skus_are_a_valid_barcode_the_syncs_own_test():
    assert srs.valid_gtin(DISHWASHER_CODE) and srs.valid_gtin(XBOX_OLD) and srs.valid_gtin("4006381333931")
    assert not srs.valid_gtin("693236535555") and not srs.valid_gtin("021551") and not srs.valid_gtin("512345678904")           # wrong check digit, too short, a coupon code
    assert srs.digits("Ama-B0H2HJ1V55-1") == "021551" and srs.digits(None) == ""


def test_the_note_says_what_onbuy_will_get_as_the_product_code():
    assert "VALID barcode" in srs.code_note(DISHWASHER_CODE) and "attaches to any product" in srs.code_note(DISHWASHER_CODE)
    assert "not a valid barcode" in srs.code_note("Ama-B0H2HJ1V55-1") and "created new" in srs.code_note("Ama-B0H2HJ1V55-1")


def test_a_sku_or_its_digits_used_on_any_tab_is_a_clash_but_the_row_itself_is_not():
    tabs = {"Sheet1": values(["404834092055", "a"], [DISHWASHER_CODE, "b"]), "Amazon": values(["Ama-X-1", "c"], ["999", "d"])}
    assert srs.clashes(tabs, DISHWASHER_CODE, "Amazon", 2) == [("Sheet1", 3)]                       # the same code on the OTHER tab
    assert srs.clashes(tabs, DISHWASHER_CODE, "Sheet1", 3) == []                                    # the row being changed is not a clash with itself
    assert srs.clashes(tabs, "Ama-X-1", "Sheet1", 2) == [("Amazon", 2)]                             # the same SKU text
    assert srs.clashes(tabs, "404834092055-x", "Amazon", 3) == [("Sheet1", 2)]                      # the same digits with a suffix = the same product code
    assert srs.clashes(tabs, "999", "Sheet1", 2) == [("Amazon", 3)]                                 # a short number clashes only as the same text
    assert srs.clashes(tabs, "Ama-B0H2HJ1V55-1", "Amazon", 3) == []


# ---- the whole run ----------------------------------------------------------------------------------------------------------------------
def test_a_row_of_the_amazon_tab_gets_its_new_sku_and_its_onbuy_state_cleared(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b)
    out = capsys.readouterr().out
    assert "tab: Amazon" in out and "not a valid barcode" in out and "WROTE row 3" in out and first.updates == []
    cells = {u["range"]: u["values"][0][0] for u in amazon.updates}
    assert cells["A3"] == "Ama-B0H2HJ1V55-1" and len(cells) == 8                                    # the SKU + the 7 OnBuy-state cells
    assert all(cells[f"{letter}3"] == "" for letter in "CDEFGHI")                                   # Sync Status, OPC, Last OnBuy Sync, Product Created, Listing Active, Product ID, Last Checked Time


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b, DRY_RUN=True)
    assert "DRY RUN - nothing written" in capsys.readouterr().out and amazon.updates == [] and first.updates == []


def test_a_code_that_another_row_of_either_tab_has_is_refused(monkeypatch):
    b, first, amazon = book()
    with pytest.raises(SystemExit, match="already used"):
        run(monkeypatch, b, NEW_SKU="404834092055")                                                  # the main tab's SKU
    with pytest.raises(SystemExit, match="already used"):
        run(monkeypatch, b, NEW_SKU="344677307704-zzz")                                              # the Amazon tab's digits with a non-digit suffix (a digit would make another code)
    assert amazon.updates == [] and first.updates == []


def test_the_guards_on_the_row_still_abort(monkeypatch):
    b, first, amazon = book()
    with pytest.raises(SystemExit, match="expected SKU"):
        run(monkeypatch, b, EXPECT_SKU="not this one")
    with pytest.raises(SystemExit, match="expected title"):
        run(monkeypatch, b, EXPECT_TITLE="A kettle")
    with pytest.raises(SystemExit, match="outside the sheet"):
        run(monkeypatch, b, ROW=99)
    assert amazon.updates == []


def test_without_a_tab_the_main_product_tab_is_used_as_before(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b, SHEET_TAB="", ROW=2, EXPECT_SKU="404834092055", EXPECT_TITLE="A kettle")
    assert "tab: Sheet1" in capsys.readouterr().out and first.updates and amazon.updates == []


def test_the_workflow_takes_the_tab_and_keeps_the_dry_run_default():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "set_row_sku.yml").read_text(encoding="utf-8"))
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"row", "tab", "new_sku", "expect_sku", "expect_title", "dry_run"} and inputs["tab"]["default"] == "" and inputs["dry_run"]["default"] == "yes"
    step = next(s for s in doc["jobs"]["fix"]["steps"] if s.get("name") == "Set SKU")
    assert step["env"]["SHEET_TAB"] == "${{ inputs.tab }}" and step["env"]["NEW_SKU"] == "${{ inputs.new_sku }}"
