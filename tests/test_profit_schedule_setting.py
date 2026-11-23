"""PROFIT_SCHEDULE (the dashboard's schedule setting) replaces the schedule constants; a malformed value falls back to them; the code's
own schedule is kept as superseded so prices written under it stay the automation's own."""
import json

import pricing


def install(raw):
    return pricing._apply_schedule_setting(pricing._schedule_setting(raw))


def test_setting_replaces_the_bands_and_keeps_the_code_schedule_as_superseded():
    code_bands, code_superseded = pricing.PROFIT_BANDS, pricing._SUPERSEDED_PROFIT_BANDS
    old = [[5, 100], [10, 80], [50, 40], [None, 20]]
    try:
        install(json.dumps({"bands": [[5, 50], [10, 50], [30, 50], [None, 50]], "superseded": [old]}))
        assert pricing.PROFIT_BANDS == ((5.0, 50.0), (10.0, 50.0), (30.0, 50.0), (None, 50.0))
        assert pricing.profit_percent(3) == 50 and pricing.profit_percent(29) == 50 and pricing.profit_percent(100) == 50
        assert ((5.0, 100.0), (10.0, 80.0), (50.0, 40.0), (None, 20.0)) in pricing._SUPERSEDED_PROFIT_BANDS
        assert code_bands in pricing._SUPERSEDED_PROFIT_BANDS
        # a price the automation wrote under the code schedule is recognised: its profit is among the legacy values for that cost
        for cost in (3.0, 7.0, 20.0, 80.0):
            was = pricing._band_lookup(code_bands, cost)
            if was != 50:
                assert was in pricing.legacy_profit_percents(cost)
    finally:
        install(None)
    assert pricing.PROFIT_BANDS == code_bands and pricing._SUPERSEDED_PROFIT_BANDS == code_superseded


def test_malformed_or_empty_setting_keeps_the_code_schedule():
    code_bands = pricing.PROFIT_BANDS
    bad = ["", "not json", json.dumps([1, 2]), json.dumps({"bands": []}), json.dumps({"bands": [[5, 20], [None, 20], [10, 20]]}),
           json.dumps({"bands": [[5, 20], [10, 2000], [None, 20]]}), json.dumps({"bands": [[10, 20], [5, 20], [None, 20]]}),
           json.dumps({"bands": [[5, 20], [10, 20]]}), json.dumps({"bands": [[0, 20], [None, 20]]})]
    for raw in bad:
        assert pricing._schedule_setting(raw) is None, raw
        install(raw)
        assert pricing.PROFIT_BANDS == code_bands, raw
    install(None)


def test_parser_reads_overrides_and_rejects_bad_windows():
    ok = pricing._schedule_setting(json.dumps({"bands": [[5, 100], [10, 80], [None, 20]], "sell_price_overrides": [[10, 20, 15]]}))
    assert ok["bands"] == ((5.0, 100.0), (10.0, 80.0), (None, 20.0)) and ok["overrides"] == ((10.0, 20.0, 15.0),)
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 100], [None, 20]]}))["overrides"] is None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 100], [None, 20]], "sell_price_overrides": [[20, 10, 15]]})) is None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 100], [None, 20]], "sell_price_overrides": [[10, 20]]})) is None
    # a superseded entry that does not parse is dropped, the rest kept
    partial = pricing._schedule_setting(json.dumps({"bands": [[5, 100], [None, 20]], "superseded": [[[5, 1]], [[5, 80], [None, 40]]]}))
    assert partial["superseded"] == (((5.0, 80.0), (None, 40.0)),)


def test_until_date_is_inclusive_and_the_setting_ends_by_itself(monkeypatch):
    # 2026-11-23 (user: "15% profit on GTV all products till 30th nov"): the setting holds through its UTC day, then reads as unset.
    from datetime import datetime as real_datetime, timezone as tz

    class FakeDatetime(real_datetime):
        today = real_datetime(2026, 11, 30, 23, 59, tzinfo=tz.utc)

        @classmethod
        def now(cls, tzinfo=None):
            return cls.today

    monkeypatch.setattr(pricing, "datetime", FakeDatetime)
    raw = json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]], "until": "2026-11-30"})
    assert pricing._schedule_setting(raw)["bands"] == ((5.0, 15.0), (10.0, 15.0), (50.0, 15.0), (None, 15.0))   # last day: still on
    FakeDatetime.today = real_datetime(2026, 12, 1, 0, 0, tzinfo=tz.utc)
    assert pricing._schedule_setting(raw)["expired"] is True                                                    # next day: ended
    code_bands = pricing.PROFIT_BANDS
    assert install(raw) is None                                                                                # ... = the code constants
    assert pricing.PROFIT_BANDS == code_bands
    # no / empty until = permanent; a malformed date never breaks a run (falls back to the constants)
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]]})) is not None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]], "until": ""})) is not None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]], "until": "30th nov"})) is None
    install(None)


def _temp_raise_setting():
    return json.dumps({"bands": [[5, 115], [10, 95], [50, 55], [None, 35]], "sell_price_overrides": [[10, 20, 30]],
                       "anchor_on_code_bands": True, "superseded": [[[5, 15], [10, 15], [50, 15], [None, 15]]],
                       "until": "2026-11-30"})


def test_plus_15_schedule_keeps_the_window_on_the_same_rows_as_the_code_schedule():
    # 2026-11-23: every band +15 points (100->115, 80->95, 40->55, 20->35) and the GBP 10-20 window 15 -> 30. The window is anchored on
    # the CODE bands, so exactly the rows that sit in the window today are in it - none slide in or out when the bands move.
    costs = [round(0.5 * n, 2) for n in range(1, 400)]          # GBP 0.50 .. 199.50
    try:
        install(None)
        code_profit = {c: pricing.profit_percent_for(c) for c in costs}
        install(_temp_raise_setting())
        in_window_now = {c for c in costs if code_profit[c] == 15 and pricing.profit_percent(c) != 15}
        assert in_window_now, "the code window should cover some costs"
        for c in costs:
            expected = {100: 115, 80: 95, 40: 55, 20: 35}.get(code_profit[c], 30 if c in in_window_now else code_profit[c])
            assert pricing.profit_percent_for(c) == expected, (c, code_profit[c], pricing.profit_percent_for(c))
    finally:
        install(None)


def test_an_ended_plus_15_schedule_still_counts_as_the_automations_own(monkeypatch):
    # After 30 Nov the prices written at +15 are ABOVE the code formula; they must be recognised (superseded) so the sync lowers them again.
    from datetime import datetime as real_datetime, timezone as tz

    class FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tzinfo=None):
            return real_datetime(2026, 12, 2, 1, 0, tzinfo=tz.utc)

    monkeypatch.setattr(pricing, "datetime", FakeDatetime)
    code_bands = pricing.PROFIT_BANDS
    try:
        assert install(_temp_raise_setting()) is None
        assert pricing.PROFIT_BANDS == code_bands
        assert ((5.0, 115.0), (10.0, 95.0), (50.0, 55.0), (None, 35.0)) in pricing._SUPERSEDED_PROFIT_BANDS
        assert 30.0 in pricing._SUPERSEDED_OVERRIDE_PCTS
        # cost 3 (code 100, temp 115), 7 (80 / 95), 30 (40 / 55), 80 (20 / 35): the temp profit is a legacy value for that cost
        for cost, temp in ((3.0, 115.0), (7.0, 95.0), (30.0, 55.0), (80.0, 35.0)):
            assert temp in pricing.legacy_profit_percents(cost), cost
        # a window-row price written at 30% is recognised too (cost 12: code window 15%)
        window_cost = next(c for c in (x / 2 for x in range(2, 200)) if pricing.profit_percent_for(c) == 15)
        assert 30.0 in pricing.legacy_profit_percents(window_cost), window_cost
        import generate_xml
        for cost, temp in ((3.0, 115.0), (30.0, 55.0), (80.0, 35.0)):
            price = pricing.price_for_profit(cost, temp, legacy=False)
            assert generate_xml._formula_priced(price, cost, 0.0, None), (cost, temp)
    finally:
        install(None)
