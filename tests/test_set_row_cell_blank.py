"""set_row_cell (2026-10-28): the value "(blank)" clears a cell. GitHub refuses an empty value for a required workflow input, and a person's typed Fee % / Profit % (GTV Amazon row 5198 carried a
typed Fee % 18.00 that the sync reads as an override and never overwrites) has to be removable without a hand edit in the sheet."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import set_row_cell as src

ROOT = Path(__file__).resolve().parents[1]
HEADER = ["SKU", "Title", "Fee %", "Profit %"]


class Sheet:
    def __init__(self, title, rows):
        self.title, self.vals, self.writes = title, [HEADER] + rows, []

    def get_all_values(self):
        return self.vals

    def update_acell(self, address, value):
        self.writes.append((address, value))


class Book:
    def __init__(self, *sheets):
        self.sheets = list(sheets)
        self.sheet1 = self.sheets[0]

    def worksheet(self, name):
        return next(s for s in self.sheets if s.title == name)


def run(monkeypatch, book, **env):
    class Client:
        def open(self, name):
            return book

    monkeypatch.setenv("GOOGLE_CREDENTIALS", "{}")
    monkeypatch.setattr(src.ServiceAccountCredentials, "from_json_keyfile_dict", staticmethod(lambda *a, **k: object()))
    monkeypatch.setattr(src.gspread, "authorize", lambda creds: Client())
    for key, value in {"ROW": 3, "COLUMN": "Fee %", "VALUE": "(blank)", "EXPECT_SKU": "Ama-B0H2HJ1V55-1", "SHEET_TAB": "Amazon", "DRY_RUN": False, **env}.items():
        monkeypatch.setattr(src, key, value)
    src.main()


def book():
    first = Sheet("Sheet1", [["404834092055", "A kettle", "8.40", "20"]])
    amazon = Sheet("Amazon", [["344677307704", "A mug", "8.40", "20"], ["Ama-B0H2HJ1V55-1", "Xbox Series X", "18.00", "20"]])
    return Book(first, amazon), first, amazon


def test_the_word_blank_clears_a_typed_cell(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b)
    out = capsys.readouterr().out
    assert amazon.writes == [("C3", "")] and first.writes == []
    assert "Fee % old: '18.00'" in out and "(cleared)" in out and "WROTE row 3 Fee % (cleared)" in out


def test_the_clear_word_is_case_and_space_tolerant(monkeypatch):
    for word in ("(CLEAR)", "  (Blank) "):
        b, first, amazon = book()
        run(monkeypatch, b, VALUE=word)
        assert amazon.writes == [("C3", "")]


def test_an_ordinary_value_is_still_written_as_typed(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b, VALUE="9.60")
    assert amazon.writes == [("C3", "9.60")] and "WROTE row 3 Fee %" in capsys.readouterr().out
    b, first, amazon = book()
    run(monkeypatch, b, VALUE="blank")                                   # only the bracketed words clear; plain text is a value
    assert amazon.writes == [("C3", "blank")]


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    b, first, amazon = book()
    run(monkeypatch, b, DRY_RUN=True)
    assert amazon.writes == [] and "DRY RUN - nothing written" in capsys.readouterr().out


def test_clearing_a_cell_that_is_already_blank_is_a_no_op(monkeypatch, capsys):
    b, first, amazon = book()
    amazon.vals[2][2] = ""
    run(monkeypatch, b)
    assert amazon.writes == [] and "already correct" in capsys.readouterr().out


def test_the_sku_guard_still_protects_the_row(monkeypatch):
    b, first, amazon = book()
    with pytest.raises(SystemExit, match="expected SKU"):
        run(monkeypatch, b, EXPECT_SKU="not this one")
    assert amazon.writes == []


def test_the_workflow_keeps_the_value_required_and_names_the_word():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "set_row_cell.yml").read_text(encoding="utf-8"))
    value = doc[True]["workflow_dispatch"]["inputs"]["value"]
    assert value["required"] is True and "(blank)" in value["description"]
