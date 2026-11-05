"""One-off, surgical: put the Amazon tab's SKU column back from an earlier Drive revision of the sheet (2026-11-05).

2026-11-04 the whole SKU column (column A) of GTV's Amazon tab disappeared: row 1 now starts at 'Title', so every Amazon sync and every
backfill stops at the header guard ("missing header(s): SKU") and the oversell guard cannot read the tab. Nothing else is rolled back
here. This reads ONE earlier revision of the spreadsheet through the Drive API (read-only), proves that the tab then looked exactly like
today's tab plus a SKU column, and writes just that column back: insert column A + header + the old values in ONE atomic batchUpdate
(the tab's other columns shift one place to the right again, as before).

Env:
  ACTION          list | find | plan | apply | diff   (default list; only apply writes)
  TAB             worksheet name                   (default Amazon)
  SINCE           ISO time; older revisions are ignored   (default 2026-11-04T06:00:00Z)
  REVISION        Drive revision id for plan/apply; empty = the last revision that still has a SKU header (found by bisection)
  REVISION2       diff only - the second revision to compare REVISION against (every tab, by header name)
  CONFIRM         apply only - must be "restore"
  MAX_UNASSIGNED  apply only - most rows allowed to stay without a SKU (default 25)
"""
import io
import json
import os
import re
import sys
from datetime import date, datetime

SHEET_NAME = "OnBuy_Feed_Master"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRIVE = "https://www.googleapis.com/drive/v3"
# Text columns that identify a row in both the earlier .xlsx and the live sheet (numbers/dates are formatted differently in the two).
KEY_COLS = ("Supplier URL", "ASIN", "OPC", "Title")
MAX_SAFE_INT = 10 ** 15


# ---------------------------------------------------------------- pure helpers (unit-tested)
def norm(value):
    """One comparable string for a cell, whether it came from the .xlsx (numbers, booleans) or from the live sheet (display text)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def cell(row, j):
    return row[j] if j < len(row) else None


def trim_row(row):
    """A row without its trailing empty cells."""
    row = list(row)
    while row and norm(row[-1]) == "":
        row.pop()
    return row


def trim_rows(rows):
    """The rows without trailing all-empty rows."""
    rows = [list(r) for r in rows]
    while rows and not any(norm(c) for c in rows[-1]):
        rows.pop()
    return rows


def sku_cell(value):
    """('number', n) | ('string', s) | None (blank) - keeps the cell type the earlier revision had."""
    if value is None:
        return None
    if isinstance(value, bool):
        return ("string", "TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        if float(value).is_integer() and abs(value) < MAX_SAFE_INT:
            return ("number", int(value))
        return ("string", str(int(value)) if float(value).is_integer() else repr(value))
    text = str(value).strip()
    return ("string", text) if text else None


def sku_text(typed):
    """The text the sync will read back for a typed SKU cell (commas removed, like the sync does)."""
    return "" if typed is None else str(typed[1]).replace(",", "").strip()


def same_cell(a, b):
    """Do an earlier-revision cell (python value from the .xlsx) and today's displayed text mean the same? Dates are not compared (formats differ)."""
    if isinstance(a, (datetime, date)):
        return True
    na, nb = norm(a), norm(b)
    if na == nb:
        return True

    def num(x):
        try:
            return float(re.sub(r"[£$€%,\s]", "", x))
        except ValueError:
            return None
    fa, fb = num(na), num(nb)
    return fa is not None and fb is not None and abs(fa - fb) < 1e-6


def plan(old_header, old_rows, cur_header, cur_rows, key_cols=KEY_COLS, sku_formulas=0):
    """Pair every row of today's tab with its row in the earlier revision. Rows are lists of cell values (data rows only).
    Pass 1 = same position AND same key; pass 2 = a unique unused row with the same key (a row moved or was deleted since).
    Returns {ok, problems, skus (typed cell | None per current row), stats}."""
    old_h = [norm(h) for h in trim_row(old_header)]
    cur_h = [norm(h) for h in trim_row(cur_header)]
    out = {"ok": False, "problems": [], "skus": [], "stats": {}}
    if old_h.count("SKU") != 1:
        out["problems"].append(f"the earlier revision's header has {old_h.count('SKU')} SKU column(s), need exactly 1")
        return out
    sku_i = old_h.index("SKU")
    old_wo = old_h[:sku_i] + old_h[sku_i + 1:]
    if old_wo != cur_h:
        diffs = [f"col {i + 1}: then {a!r} / now {b!r}" for i, (a, b) in enumerate(zip(old_wo, cur_h)) if a != b][:5]
        if len(old_wo) != len(cur_h):
            diffs.append(f"column count then {len(old_wo)} (without SKU) / now {len(cur_h)}")
        out["problems"].append("the tab's layout then differs from today's apart from the SKU column: " + "; ".join(diffs))
        return out
    cols = [c for c in key_cols if c in cur_h]
    if not cols:
        out["problems"].append(f"none of the key columns {key_cols} exist in the header")
        return out
    cj = [cur_h.index(c) for c in cols]

    def old_cell(row, j):
        return cell(row, j if j < sku_i else j + 1)

    old_keys = [tuple(norm(old_cell(r, j)) for j in cj) for r in old_rows]
    cur_keys = [tuple(norm(cell(r, j)) for j in cj) for r in cur_rows]
    pair = [None] * len(cur_rows)
    used = set()
    positional = 0
    for i, k in enumerate(cur_keys):                                   # pass 1
        if any(k) and i < len(old_keys) and old_keys[i] == k:
            pair[i] = i
            used.add(i)
            positional += 1
    by_key = {}
    for n, k in enumerate(old_keys):
        if any(k) and n not in used:
            by_key.setdefault(k, []).append(n)
    moved = 0
    for i, k in enumerate(cur_keys):                                   # pass 2
        if pair[i] is None and any(k):
            hits = by_key.get(k, [])
            if len(hits) == 1 and hits[0] not in used:
                pair[i] = hits[0]
                used.add(hits[0])
                moved += 1
    skus, unassigned, blank_rows = [], [], 0
    for i, n in enumerate(pair):
        if not any(cur_keys[i]):
            skus.append(None)
            blank_rows += 1
        elif n is None:
            skus.append(None)
            unassigned.append(i + 2)
        else:
            skus.append(sku_cell(cell(old_rows[n], sku_i)))
    by_text = {}
    for i, typed in enumerate(skus):
        if typed is not None:
            by_text.setdefault(sku_text(typed), []).append(i + 2)
    old_counts = {}
    for row in old_rows:
        t = sku_text(sku_cell(cell(row, sku_i)))
        if t:
            old_counts[t] = old_counts.get(t, 0) + 1
    dupes = {t: rows for t, rows in by_text.items() if len(rows) > 1}
    invented = {t: rows for t, rows in dupes.items() if len(rows) > old_counts.get(t, 0)}
    out["warnings"] = [f"SKU {t!r} sits on {len(rows)} rows (sheet rows {rows}) - the earlier revision had it that way too"
                       for t, rows in dupes.items() if t not in invented]
    formulas = int(sku_formulas)
    if invented:
        out["problems"].append(f"{len(invented)} SKU value(s) would appear on MORE rows than in the earlier revision, e.g. {sorted(invented)[:5]} - a pairing bug")
    if formulas:
        out["problems"].append(f"{formulas} SKU cell(s) in the earlier revision are FORMULAS - values alone would not restore the design")
    real_rows = len(cur_rows) - blank_rows
    col_diffs = {}
    for i, n in enumerate(pair):
        if n is None:
            continue
        for j, name in enumerate(cur_h):
            if not same_cell(old_cell(old_rows[n], j), cell(cur_rows[i], j)):
                hit = col_diffs.setdefault(name, {"rows": 0, "examples": []})
                hit["rows"] += 1
                if len(hit["examples"]) < 2:
                    hit["examples"].append((i + 2, norm(old_cell(old_rows[n], j))[:40], norm(cell(cur_rows[i], j))[:40]))
    out["stats"] = {
        "dupes": dupes, "col_diffs": col_diffs,
        "sku_position": sku_i + 1, "rows_now": real_rows, "rows_then": sum(1 for k in old_keys if any(k)),
        "positional": positional, "moved": moved, "unassigned": unassigned,
        "old_blank": sum(1 for i, n in enumerate(pair) if n is not None and skus[i] is None),
        "unused_old": sum(1 for n, k in enumerate(old_keys) if any(k) and n not in used),
        "numbers": sum(1 for s in skus if s and s[0] == "number"), "strings": sum(1 for s in skus if s and s[0] == "string"),
        "key_columns": cols,
    }
    out["skus"] = skus
    out["ok"] = not out["problems"]
    return out


def build_requests(sheet_id, skus):
    """ONE batchUpdate body: insert column A, then write the header and the values. Applied atomically by Google."""
    rows = [{"values": [{"userEnteredValue": {"stringValue": "SKU"}}]}]
    for typed in skus:
        if typed is None:
            rows.append({"values": [{}]})
        elif typed[0] == "number":
            rows.append({"values": [{"userEnteredValue": {"numberValue": typed[1]}}]})
        else:
            rows.append({"values": [{"userEnteredValue": {"stringValue": typed[1]}}]})
    return [
        {"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "inheritFromBefore": False}},
        {"updateCells": {"rows": rows, "fields": "userEnteredValue", "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0}}},
    ]


def find_boundary(revs, has_sku):
    """revs oldest-first; SKU present early, absent late. Returns (index of the last revision WITH a SKU header, index of the first without)."""
    if len(revs) < 2:
        raise RuntimeError("need at least two revisions in the window")
    if not has_sku(revs[0]):
        raise RuntimeError("even the oldest revision in the window has no SKU header - move SINCE earlier")
    if has_sku(revs[-1]):
        raise RuntimeError("the newest revision still has a SKU header - the Drive revision list is behind the sheet")
    lo, hi = 0, len(revs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if has_sku(revs[mid]):
            lo = mid
        else:
            hi = mid
    return lo, hi


def diff_tabs(a_header, a_rows, b_header, b_rows, examples=3):
    """Compare two snapshots of one tab by header NAME (rows by position). Returns removed/added column names, row counts and, per common
    column, how many cells differ (with a few examples) - what an edit session between the two revisions changed."""
    a_h = [norm(h) for h in trim_row(a_header)]
    b_h = [norm(h) for h in trim_row(b_header)]
    a_ix = {h: j for j, h in enumerate(a_h) if h}
    b_ix = {h: j for j, h in enumerate(b_h) if h}
    common = [h for h in a_h if h and h in b_ix]
    changed = {}
    for i in range(min(len(a_rows), len(b_rows))):
        for h in common:
            va, vb = norm(cell(a_rows[i], a_ix[h])), norm(cell(b_rows[i], b_ix[h]))
            if va != vb:
                hit = changed.setdefault(h, {"rows": 0, "examples": []})
                hit["rows"] += 1
                if len(hit["examples"]) < examples:
                    hit["examples"].append((i + 2, va[:40], vb[:40]))
    return {"removed": [h for h in a_h if h and h not in b_ix], "added": [h for h in b_h if h and h not in a_ix],
            "rows": (len(a_rows), len(b_rows)), "cols": (len(a_h), len(b_h)), "changed": changed}


# ---------------------------------------------------------------- Google IO
def _clients():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.environ["GOOGLE_CREDENTIALS"]), scope)
    return creds, gspread.authorize(creds)


def _auth(creds):
    return {"Authorization": f"Bearer {creds.get_access_token().access_token}"}


def list_revisions(creds, file_id):
    import requests
    out, token = [], None
    fields = "nextPageToken,revisions(id,modifiedTime,size,keepForever,lastModifyingUser(displayName,emailAddress),exportLinks)"
    while True:
        params = {"pageSize": 1000, "fields": fields}
        if token:
            params["pageToken"] = token
        resp = requests.get(f"{DRIVE}/files/{file_id}/revisions", params=params, headers=_auth(creds), timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Drive revisions list: HTTP {resp.status_code} {resp.text[:300]}")
        body = resp.json()
        out += body.get("revisions", [])
        token = body.get("nextPageToken")
        if not token:
            return out


def who(rev):
    user = rev.get("lastModifyingUser") or {}
    return user.get("displayName") or user.get("emailAddress") or "?"


def fetch_export(creds, rev):
    """The .xlsx export bytes of one revision."""
    import requests
    link = (rev.get("exportLinks") or {}).get(XLSX)
    if not link:
        raise RuntimeError(f"revision {rev.get('id')} has no .xlsx export link (it offers: {sorted((rev.get('exportLinks') or {}))})")
    resp = requests.get(link, headers=_auth(creds), timeout=900)
    if resp.status_code != 200:
        raise RuntimeError(f"export of revision {rev.get('id')}: HTTP {resp.status_code} {resp.text[:300]}")
    print(f"  downloaded revision {rev.get('id')} ({len(resp.content) / 1e6:.1f} MB)")
    return resp.content


def old_tab(creds, rev, tab):
    """(header, data rows, number of SKU cells that are formulas) of one tab in an earlier revision. Values come from the cached results."""
    import openpyxl
    blob = fetch_export(creds, rev)
    wb_v = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    if tab not in wb_v.sheetnames:
        raise RuntimeError(f"revision {rev.get('id')} has no tab {tab!r} (tabs: {wb_v.sheetnames})")
    vals = [list(r) for r in wb_v[tab].iter_rows(values_only=True)]
    header = trim_row(vals[0]) if vals else []
    rows = trim_rows(vals[1:])
    names = [norm(h) for h in header]
    formulas = 0
    if "SKU" in names:
        col = names.index("SKU") + 1
        wb_f = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=False)
        for r in wb_f[tab].iter_rows(min_row=2, min_col=col, max_col=col, values_only=True):
            v = r[0]
            if (isinstance(v, str) and v.startswith("=")) or type(v).__name__ == "ArrayFormula":
                formulas += 1
    return header, rows, formulas


def make_has_sku(creds, tab):
    memo = {}

    def has_sku(rev):
        rid = rev["id"]
        if rid not in memo:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(fetch_export(creds, rev)), read_only=True, data_only=True)
            first = next(wb[tab].iter_rows(min_row=1, max_row=1, values_only=True), ()) if tab in wb.sheetnames else ()
            memo[rid] = "SKU" in [norm(c) for c in first]
            print(f"  revision {rid} ({rev['modifiedTime']}, {who(rev)}): SKU header {'PRESENT' if memo[rid] else 'absent'}")
        return memo[rid]
    return has_sku


def main():
    from retry_utils import with_retry
    action = (os.getenv("ACTION") or "list").strip().lower()
    tab_name = (os.getenv("TAB") or "Amazon").strip()
    since = (os.getenv("SINCE") or "2026-11-04T06:00:00Z").strip()
    wanted = (os.getenv("REVISION") or "").strip()
    if action not in ("list", "find", "plan", "apply", "diff"):
        raise SystemExit(f"ACTION must be list, find, plan, apply or diff, got {action!r}")
    if action == "apply" and (os.getenv("CONFIRM") or "").strip().lower() != "restore":
        raise SystemExit("apply needs CONFIRM=restore")
    creds, gc = _clients()
    book = with_retry(lambda: gc.open(SHEET_NAME), what="sheet open", max_attempts=3)
    revs = sorted((r for r in list_revisions(creds, book.id) if r["modifiedTime"] >= since), key=lambda r: r["modifiedTime"])
    print(f"{len(revs)} revision(s) since {since}")
    if action == "list":
        for r in revs:
            print(f"{r['id']}\t{r['modifiedTime']}\t{who(r)}\tsize={r.get('size', '?')}\texport={'xlsx' if XLSX in (r.get('exportLinks') or {}) else 'NO'}")
        return

    if action == "diff":
        import openpyxl
        wanted2 = (os.getenv("REVISION2") or "").strip()
        pick = {r["id"]: r for r in revs}
        if wanted not in pick or wanted2 not in pick:
            raise SystemExit(f"diff needs REVISION and REVISION2 from the list since {since}; got {wanted!r} / {wanted2!r}")
        books = []
        for rid in (wanted, wanted2):
            print(f"revision {rid}: {pick[rid]['modifiedTime']} by {who(pick[rid])}")
            books.append(openpyxl.load_workbook(io.BytesIO(fetch_export(creds, pick[rid])), read_only=True, data_only=True))
        names = list(books[0].sheetnames) + [n for n in books[1].sheetnames if n not in books[0].sheetnames]
        print(f"tabs then: {books[0].sheetnames}")
        print(f"tabs now : {books[1].sheetnames}")
        for name in names:
            if name not in books[0].sheetnames or name not in books[1].sheetnames:
                print(f"[{name}] exists only in {'the second' if name not in books[0].sheetnames else 'the first'} revision")
                continue
            snaps = []
            for wb in books:
                rows = [list(r) for r in wb[name].iter_rows(values_only=True)]
                snaps.append((trim_row(rows[0]) if rows else [], trim_rows(rows[1:])))
            d = diff_tabs(snaps[0][0], snaps[0][1], snaps[1][0], snaps[1][1])
            total = sum(v["rows"] for v in d["changed"].values())
            print(f"[{name}] rows {d['rows'][0]} -> {d['rows'][1]}, columns {d['cols'][0]} -> {d['cols'][1]}, removed {d['removed']}, added {d['added']}, "
                  f"changed cells {total} in {len(d['changed'])} column(s)")
            for h, v in sorted(d["changed"].items(), key=lambda kv: -kv[1]["rows"])[:8]:
                print(f"    {h}: {v['rows']} cell(s), e.g. " + "; ".join(f"row {r} {a!r} -> {b!r}" for r, a, b in v["examples"]))
        return

    if action == "find" or not wanted:
        last_with, first_without = find_boundary(revs, make_has_sku(creds, tab_name))
        a, b = revs[last_with], revs[first_without]
        print(f"LAST revision WITH a SKU header: {a['id']} at {a['modifiedTime']} by {who(a)}")
        print(f"FIRST revision WITHOUT it:       {b['id']} at {b['modifiedTime']} by {who(b)}   <- whoever edited last in this revision removed the column")
        if action == "find":
            return
        rev = a
    else:
        rev = next((r for r in revs if r["id"] == wanted), None)
        if rev is None:
            raise SystemExit(f"revision {wanted!r} is not in the list since {since}")

    ws = book.worksheet(tab_name)
    cur = with_retry(ws.get_all_values, what=f"read {tab_name}", max_attempts=3)
    cur_header, cur_rows = trim_row(cur[0]), trim_rows(cur[1:])
    if "SKU" in [norm(h) for h in cur_header]:
        print(f"[{tab_name}] already has a SKU column - nothing to do")
        return
    if norm(cur_header[0] if cur_header else "") != "Title":
        raise SystemExit(f"[{tab_name}] A1 is {cur_header[:1]!r}, expected 'Title' - the tab is not in the state this tool was written for; stopping")
    print(f"[{tab_name}] today: {len(cur_header)} columns, {len(cur_rows)} data rows; using revision {rev['id']} ({rev['modifiedTime']} by {who(rev)})")
    old_header, old_rows, sku_formulas = old_tab(creds, rev, tab_name)
    result = plan(old_header, old_rows, cur_header, cur_rows, sku_formulas=sku_formulas)
    st = result["stats"]
    for p in result["problems"]:
        print(f"PROBLEM: {p}")
    for w in result.get("warnings", []):
        print(f"WARNING: {w}")
    if st:
        print(f"SKU was column {st['sku_position']} | rows then {st['rows_then']} / now {st['rows_now']} | paired by position {st['positional']}, "
              f"by key {st['moved']} | NOT paired {len(st['unassigned'])} | paired but blank SKU then {st['old_blank']} | old rows now gone {st['unused_old']}")
        print(f"SKU cells: {st['numbers']} numbers, {st['strings']} text | key columns used {st['key_columns']}")
        kinds = {"12-digit": 0, "Ama-ASIN": 0, "other": 0}
        for s in result["skus"]:
            t = sku_text(s)
            if t:
                kinds["12-digit" if re.fullmatch(r"\d{12}", t) else "Ama-ASIN" if t.startswith("Ama-") else "other"] += 1
        print(f"SKU shapes: {kinds}")
        if st["col_diffs"]:
            print("columns whose cells differ between then and now (rows): "
                  + ", ".join(f"{k} {v['rows']}" for k, v in sorted(st["col_diffs"].items(), key=lambda kv: -kv[1]["rows"])))
            for k, v in sorted(st["col_diffs"].items(), key=lambda kv: -kv[1]["rows"])[:6]:
                print(f"  e.g. {k}: " + "; ".join(f"row {r} then {a!r} now {b!r}" for r, a, b in v["examples"]))
        else:
            print("no other cell differs between then and now (dates not compared)")
        wanted_cols = [c for c in ("Title", "Supplier URL", "ASIN", "OPC", "Sync Status", "Stock", "Status", "Selling Price (£)", "OnBuy Product Created") if c in cur_header]
        for t, rows_ in st["dupes"].items():
            for rn in rows_:
                row_ = cur_rows[rn - 2]
                print(f"  duplicate {t!r} row {rn}: " + " | ".join(f"{c}={norm(cell(row_, cur_header.index(c)))[:45]!r}" for c in wanted_cols))
        odd = [(i + 2, sku_text(x)) for i, x in enumerate(result["skus"]) if x is not None and not re.fullmatch(r"\d{12}", sku_text(x)) and not sku_text(x).startswith("Ama-")]
        if odd:
            print(f"SKUs that are neither 12 digits nor Ama-...: {len(odd)}: " + ", ".join(f"r{rn}:{t}" for rn, t in odd[:45]))
        if st["unassigned"]:
            print(f"rows without a pair (sheet row numbers): {st['unassigned'][:40]}{' ...' if len(st['unassigned']) > 40 else ''}")
        title_j = cur_header.index("Title") if "Title" in cur_header else 0
        picks = sorted(set(list(range(0, min(4, len(cur_rows)))) + [len(cur_rows) // 2, len(cur_rows) // 3, len(cur_rows) - 1]))
        for i in picks:
            print(f"  row {i + 2}: SKU {sku_text(result['skus'][i])!r:24} title {norm(cell(cur_rows[i], title_j))[:60]!r}")
    if action == "plan":
        print("PLAN ONLY - nothing was written" + ("" if result["ok"] else " (and apply would be refused)"))
        return

    limit = int(os.getenv("MAX_UNASSIGNED") or "25")
    if not result["ok"]:
        raise SystemExit("refusing to write: see PROBLEM lines above")
    if len(st["unassigned"]) > limit:
        raise SystemExit(f"refusing to write: {len(st['unassigned'])} rows have no pair (limit {limit}); raise MAX_UNASSIGNED only after looking at them")
    fresh = with_retry(ws.get_all_values, what=f"re-read {tab_name}", max_attempts=3)
    if trim_row(fresh[0]) != cur_header or trim_rows(fresh[1:]) != cur_rows:
        raise SystemExit("the tab changed while this run was planning - nothing written; run it again")
    body = {"requests": build_requests(ws.id, result["skus"])}
    with_retry(lambda: book.batch_update(body), what="insert SKU column + values", max_attempts=1)
    print(f"WROTE: inserted column A and {sum(1 for s in result['skus'] if s is not None)} SKU value(s) in one batchUpdate")

    after = with_retry(ws.get_all_values, what=f"read back {tab_name}", max_attempts=3)
    a_header, a_rows = trim_row(after[0]), trim_rows(after[1:])
    problems = []
    if a_header != ["SKU"] + cur_header:
        problems.append("header after the write is not ['SKU'] + the old header")
    if [trim_row(r[1:]) for r in a_rows] != [trim_row(r) for r in cur_rows]:
        problems.append("the other columns changed")
    got = [str(r[0]).replace(",", "").strip() if r else "" for r in a_rows]
    want = [sku_text(s) for s in result["skus"]]
    bad = [i + 2 for i, (g, w) in enumerate(zip(got, want)) if g != w]
    if bad or len(got) != len(want):
        problems.append(f"{len(bad)} SKU cell(s) differ from the plan, e.g. rows {bad[:10]}")
    if problems:
        print("READ-BACK PROBLEMS: " + "; ".join(problems))
        sys.exit(1)
    print(f"VERIFIED: header = SKU + {len(cur_header)} old columns; all {len(want)} rows' other cells unchanged; {sum(1 for w in want if w)} SKUs match the plan")


if __name__ == "__main__":
    main()
