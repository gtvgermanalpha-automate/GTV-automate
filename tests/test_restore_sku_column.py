"""restore_sku_column.py pairs today's Amazon tab with an earlier revision that still had the SKU column, and writes only that column back.
The pairing, the cell typing and the write body are pure functions - tested here with small synthetic tabs."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import restore_sku_column as r

HEADER_NOW = ["Title", "Description", "Supplier URL", "ASIN", "OPC", "Stock"]
HEADER_THEN = ["SKU"] + HEADER_NOW


def now_row(n, opc="PA1"):
    return [f"Item {n}", f"desc {n}", f"https://amazon.co.uk/dp/B{n:09d}", f"B{n:09d}", opc, 5]


def then_row(sku, n, opc="PA1"):
    return [sku] + now_row(n, opc)


def test_every_row_pairs_by_position_when_nothing_moved():
    then = [then_row(100000000000 + n, n) for n in range(1, 6)]
    now = [now_row(n) for n in range(1, 6)]
    out = r.plan(HEADER_THEN, then, HEADER_NOW, now)
    assert out["ok"] and out["problems"] == []
    assert out["stats"]["positional"] == 5 and out["stats"]["moved"] == 0 and out["stats"]["unassigned"] == []
    assert [r.sku_text(s) for s in out["skus"]] == [str(100000000000 + n) for n in range(1, 6)]


def test_a_row_deleted_since_shifts_the_rest_and_they_still_pair_by_key():
    then = [then_row(f"Ama-B{n:09d}-1", n) for n in range(1, 6)]
    now = [now_row(n) for n in (1, 2, 4, 5)]                      # row 3 was deleted afterwards
    out = r.plan(HEADER_THEN, then, HEADER_NOW, now)
    assert out["ok"]
    assert out["stats"]["positional"] == 2 and out["stats"]["moved"] == 2 and out["stats"]["unused_old"] == 1
    assert [r.sku_text(s) for s in out["skus"]] == ["Ama-B000000001-1", "Ama-B000000002-1", "Ama-B000000004-1", "Ama-B000000005-1"]


def test_a_row_added_after_has_no_pair_and_is_listed_with_its_sheet_row_number():
    then = [then_row(f"S{n}", n) for n in range(1, 4)]
    now = [now_row(n) for n in range(1, 4)] + [now_row(9)]
    out = r.plan(HEADER_THEN, then, HEADER_NOW, now)
    assert out["ok"] and out["stats"]["unassigned"] == [5]       # data row 4 = sheet row 5
    assert out["skus"][3] is None


def test_two_identical_rows_never_steal_each_others_sku():
    then = [then_row("A-1", 1), then_row("A-2", 1)]               # same URL/ASIN/OPC/Title twice
    now = [now_row(1), now_row(1)]
    out = r.plan(HEADER_THEN, then, HEADER_NOW, now)
    assert [r.sku_text(s) for s in out["skus"]] == ["A-1", "A-2"]


def test_a_layout_that_differs_by_more_than_the_sku_column_is_refused():
    other = ["SKU", "Title", "Supplier URL", "Description", "ASIN", "OPC", "Stock"]
    out = r.plan(other, [], HEADER_NOW, [now_row(1)])
    assert not out["ok"] and "layout" in out["problems"][0]
    out = r.plan(HEADER_NOW, [], HEADER_NOW, [now_row(1)])         # earlier revision has no SKU column either
    assert not out["ok"] and "SKU" in out["problems"][0]


def test_a_repeated_sku_or_a_formula_column_stops_the_restore():
    then = [then_row("DUP", 1), then_row("DUP", 2)]
    out = r.plan(HEADER_THEN, then, HEADER_NOW, [now_row(1), now_row(2)])
    assert not out["ok"] and "more than one row" in out["problems"][0]
    ok_then = [then_row("S1", 1)]
    out = r.plan(HEADER_THEN, ok_then, HEADER_NOW, [now_row(1)], sku_formulas=3)
    assert not out["ok"] and "FORMULAS" in out["problems"][0]


def test_cell_types_follow_the_earlier_revision():
    assert r.sku_cell(175321526932.0) == ("number", 175321526932)
    assert r.sku_cell(175321526932) == ("number", 175321526932)
    assert r.sku_cell("0505327292720-zzz") == ("string", "0505327292720-zzz")
    assert r.sku_cell("0123456789012") == ("string", "0123456789012")          # leading zero survives as text
    assert r.sku_cell("  Ama-B0BRHJTQZ6-5 ") == ("string", "Ama-B0BRHJTQZ6-5")
    assert r.sku_cell(None) is None and r.sku_cell("   ") is None
    assert r.sku_cell(12345678901234567.0) == ("string", "12345678901234568")  # too big for a safe number: kept as text
    assert r.sku_text(("number", 175321526932)) == "175321526932" and r.sku_text(None) == ""


def test_the_write_is_one_insert_then_one_update_of_column_a():
    body = r.build_requests(777, [("number", 5), None, ("string", "Ama-X-1")])
    assert [list(b)[0] for b in body] == ["insertDimension", "updateCells"]
    ins = body[0]["insertDimension"]
    assert ins["range"] == {"sheetId": 777, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1} and ins["inheritFromBefore"] is False
    upd = body[1]["updateCells"]
    assert upd["start"] == {"sheetId": 777, "rowIndex": 0, "columnIndex": 0}
    cells = [row["values"][0] for row in upd["rows"]]
    assert cells == [{"userEnteredValue": {"stringValue": "SKU"}}, {"userEnteredValue": {"numberValue": 5}}, {}, {"userEnteredValue": {"stringValue": "Ama-X-1"}}]


def test_the_boundary_search_finds_the_last_revision_that_still_had_the_column():
    revs = [{"id": str(i)} for i in range(10)]
    seen = []

    def has_sku(rev):
        seen.append(rev["id"])
        return int(rev["id"]) <= 6

    lo, hi = r.find_boundary(revs, has_sku)
    assert (lo, hi) == (6, 7)
    assert len(seen) <= 6                                          # a few downloads, not one per revision


def test_the_boundary_search_refuses_a_window_that_cannot_contain_the_break():
    revs = [{"id": str(i)} for i in range(4)]
    with pytest.raises(RuntimeError, match="oldest revision"):
        r.find_boundary(revs, lambda rev: False)
    with pytest.raises(RuntimeError, match="newest revision"):
        r.find_boundary(revs, lambda rev: True)
    with pytest.raises(RuntimeError):
        r.find_boundary(revs[:1], lambda rev: True)
