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
    assert pricing._schedule_setting(raw) is None                                                              # next day: code constants
    code_bands = pricing.PROFIT_BANDS
    install(raw)
    assert pricing.PROFIT_BANDS == code_bands
    # no / empty until = permanent; a malformed date never breaks a run (falls back to the constants)
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]]})) is not None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]], "until": ""})) is not None
    assert pricing._schedule_setting(json.dumps({"bands": [[5, 15], [10, 15], [50, 15], [None, 15]], "until": "30th nov"})) is None
    install(None)
