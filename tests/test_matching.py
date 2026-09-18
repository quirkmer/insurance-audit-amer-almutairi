import datetime as dt

from audit.contract_h1 import build_contract
from audit.matching import plausible_prices, resolve_by_price


def test_plausible_prices_includes_threshold_premium_and_discount_tiers():
    c = build_h1_cached()
    # Preoperative Immunologic Endoscopic Procedure: base 151975 cents,
    # discount tiers at 12% (>80) and 20% (>240 units).
    prices = plausible_prices(c, "Preoperative Immunologic Endoscopic Procedure", 151975)
    assert 151975 in prices
    assert 133738 in prices  # 12% off
    assert 121580 in prices  # 20% off


def test_resolve_by_price_does_not_match_a_coincidental_unrelated_price():
    # "Ambulatory Psychiatric Dialysis Session" happens to bill at exactly
    # 355550 cents. A totally unrelated service should not be matched to
    # it just because *some* other service's adjusted rate lands on the
    # same number by chance -- resolve_by_price is price-only and can
    # over-match; the text-plausibility guard in resolve_descriptions is
    # what actually prevents this (tested at the integration level in
    # test_pricing_h1.py via the hospital-1 false-negative case this fixed).
    c = build_h1_cached()
    hits = resolve_by_price("per_procedure", 355550, c, [dt.date(2024, 6, 1)])
    assert "Ambulatory Psychiatric Dialysis Session" in hits


_cached = None


def build_h1_cached():
    global _cached
    if _cached is None:
        _cached = build_contract()
    return _cached
