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


def test_a_repeated_sku_that_the_earlier_revision_already_had_is_a_warning_not_a_stop():
    then = [then_row("DUP", 1), then_row("DUP", 2)]
    out = r.plan(HEADER_THEN, then, HEADER_NOW, [now_row(1), now_row(2)])
    assert out["ok"] and out["stats"]["dupes"] == {"DUP": [2, 3]}
    assert "sits on 2 rows" in out["warnings"][0]


def test_a_formula_column_stops_the_restore():
    out = r.plan(HEADER_THEN, [then_row("S1", 1)], HEADER_NOW, [now_row(1)], sku_formulas=3)
    assert not out["ok"] and "FORMULAS" in out["problems"][0]


def test_cells_that_changed_since_are_counted_per_column():
    then = [then_row("S1", 1), then_row("S2", 2)]
    now = [now_row(1), now_row(2)]
    now[1][5] = 7                                                   # Stock 5 -> 7 on the second row
    out = r.plan(HEADER_THEN, then, HEADER_NOW, now)
    assert out["ok"] and out["stats"]["col_diffs"] == {"Stock": {"rows": 1, "examples": [(3, "5", "7")]}}
    same = r.plan(HEADER_THEN, then, HEADER_NOW, [now_row(1), now_row(2)])
    assert same["stats"]["col_diffs"] == {}


def test_same_cell_ignores_number_formatting_and_dates():
    from datetime import datetime
    assert r.same_cell(12.99, "£12.99") and r.same_cell(1234, "1,234") and r.same_cell(5.0, "5")
    assert r.same_cell(datetime(2026, 11, 4, 10, 0), "04/11/2026 15:00") and not r.same_cell("a", "b") and not r.same_cell(5, "6")


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


def test_diff_names_the_removed_column_and_counts_changed_cells_by_header():
    a_head, a_rows = HEADER_THEN, [then_row("S1", 1), then_row("S2", 2)]
    b_rows = [now_row(1), now_row(2)]
    b_rows[1][5] = 9                                                 # Stock changed on the second row
    d = r.diff_tabs(a_head, a_rows, HEADER_NOW, b_rows)
    assert d["removed"] == ["SKU"] and d["added"] == [] and d["rows"] == (2, 2) and d["cols"] == (7, 6)
    assert d["changed"] == {"Stock": {"rows": 1, "examples": [(3, "5", "9")]}}
    same = r.diff_tabs(HEADER_NOW, [now_row(1)], HEADER_NOW, [now_row(1)])
    assert same["changed"] == {} and same["removed"] == [] and same["added"] == []


def test_sku_only_rows_below_the_data_are_found_and_typed():
    then = [then_row("S1", 1), then_row("S2", 2), ["175321526932"] + [None] * 6, [None] * 7, ["Ama-X-1"] + [None] * 6]
    typed, problems = r.tail_plan(HEADER_THEN, then, 2)
    assert problems == [] and typed == [("string", "175321526932"), None, ("string", "Ama-X-1")]
    typed, problems = r.tail_plan(HEADER_THEN, [then_row("S1", 1), then_row("S2", 2)], 2)
    assert typed == [] and problems == []
    _typed, problems = r.tail_plan(HEADER_THEN, [then_row("S1", 1), then_row("S2", 2), then_row("S3", 3)], 2)
    assert problems and "do not line up" in problems[0]                    # a row below holds data too: stop


def test_the_tail_write_grows_the_grid_only_when_it_has_to():
    body = r.build_tail_requests(9, 5224, [("string", "A"), None, ("number", 7)], grid_rows=6000)
    assert [list(b)[0] for b in body] == ["updateCells"] and body[0]["updateCells"]["start"] == {"sheetId": 9, "rowIndex": 5224, "columnIndex": 0}
    assert [row["values"][0] for row in body[0]["updateCells"]["rows"]] == [{"userEnteredValue": {"stringValue": "A"}}, {}, {"userEnteredValue": {"numberValue": 7}}]
    short = r.build_tail_requests(9, 5224, [("string", "A")] * 10, grid_rows=5230)
    assert [list(b)[0] for b in short] == ["appendDimension", "updateCells"] and short[0]["appendDimension"]["length"] == 4
