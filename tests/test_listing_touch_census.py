"""The bucketing of listing_touch_census (the sweep and the report are read-only and need OnBuy)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import listing_touch_census as census

NOW = datetime(2026, 11, 4, 12, 0, 0)


def _item(stamp, stock):
    return {"sku": "x", "updated_at": stamp, "stock": stock}


def test_parse_stamp_reads_onbuy_times_and_rejects_junk():
    assert census.parse_stamp("2026-11-04 10:53:33") == datetime(2026, 11, 4, 10, 53, 33)
    assert census.parse_stamp("2026-11-04 10:53:33.123") == datetime(2026, 11, 4, 10, 53, 33)
    assert census.parse_stamp("") is None and census.parse_stamp(None) is None and census.parse_stamp("n/a") is None


def test_buckets_group_by_ten_minutes_and_split_by_stock():
    items = [_item("2026-11-04 10:53:33", 0), _item("2026-11-04 10:50:01", 4), _item("2026-11-04 10:59:59", "0"),
             _item("2026-11-04 11:00:00", 5)]
    table = census.buckets(items, NOW, hours=4, bucket_min=10)
    assert table[datetime(2026, 11, 4, 10, 50)] == [3, 2, 1]
    assert table[datetime(2026, 11, 4, 11, 0)] == [1, 0, 1]


def test_buckets_ignore_old_future_and_unreadable_stamps():
    items = [_item("2026-11-03 01:00:00", 0), _item("2026-11-04 13:00:00", 0), _item("", 0), {"sku": "y"}, None]
    assert census.buckets(items, NOW, hours=6, bucket_min=10) == {}
