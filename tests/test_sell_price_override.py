"""The GBP 10-20 selling-price override (user 2026-09-26, this store only):
base-schedule prices landing in that window sell at 15% profit instead."""
import pricing


FLAT = 1 - pricing.effective_rate(pricing.PLATFORM_FEE_PERCENT) / 100.0
FLAT_ANCHOR = 1 - pricing.effective_rate(pricing.PLATFORM_FEE_PERCENT, legacy=True) / 100.0


def base_price(total):
    """The price the window is judged on: the base schedule under the fee model of 2026-09-26 (nominal + 1.5 points) -
    the VAT correction of 2026-10-07 must not move a row across the GBP 10 / 20 edges."""
    return total * (1 + pricing.profit_percent(total) / 100.0) / FLAT_ANCHOR


def test_inside_window_gets_15():
    # cost 8 -> base 80% -> ~18.95: inside the window.
    assert 10 <= base_price(8) <= 20
    assert pricing.profit_percent_for(8) == 15


def test_cheap_item_outside_window_keeps_band():
    # cost 3 -> base 100% -> ~7.89: below the window.
    assert base_price(3) < 10
    assert pricing.profit_percent_for(3) == 100


def test_expensive_item_outside_window_keeps_band():
    # cost 15 -> base 40% -> ~27.63: above the window.
    assert base_price(15) > 20
    assert pricing.profit_percent_for(15) == 40


def test_anchor_is_stable_when_override_price_leaves_window():
    # cost 4.4 -> base 100% -> ~11.58 (in window) -> 15% -> ~6.66 (below
    # window). The anchor is the BASE price, so the verdict must not flap.
    assert pricing.profit_percent_for(4.4) == 15
    at_15 = pricing.price_for_profit(4.4, 15)
    assert at_15 < 10
    assert pricing.profit_percent_for(4.4) == 15


def test_displaced_base_band_counts_as_legacy():
    # Prices written at the cost band's own 80% before the override must be
    # recognised as the automation's, so they follow the formula down.
    assert pricing.legacy_profit_percents(8)[0] == 80


def test_untouched_costs_have_no_extra_legacy():
    # cost 15: current 40%, every superseded schedule also gave 40%.
    assert pricing.legacy_profit_percents(15) == []


def test_calculate_selling_price_applies_override():
    expect = round(8 * 1.15 / FLAT, 2)
    assert pricing.calculate_selling_price(8, 0) == expect


def test_the_vat_correction_does_not_move_rows_across_the_window_edges():
    # a cost whose base price is just under GBP 10 under the window's own fee model stays at 100% profit although the
    # corrected fee would price it at 10.0x; one just under GBP 20 stays inside the window
    below = next(c / 100 for c in range(380, 440) if base_price(c / 100) < 10 <= pricing.price_for_profit(c / 100, 100))
    assert pricing.profit_percent_for(below) == 100
    inside = next(c / 100 for c in range(1100, 1300) if base_price(c / 100) <= 20 < pricing.price_for_profit(c / 100, 40))
    assert pricing.profit_percent_for(inside) == 15
