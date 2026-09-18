from audit.money import half_up_cents, parse_gbp_to_cents, parse_int, parse_percent


def test_parse_gbp_to_cents():
    assert parse_gbp_to_cents("GBP 1,301.25") == 130125
    assert parse_gbp_to_cents("GBP 68.75") == 6875


def test_parse_percent():
    assert parse_percent("+20%") == 0.20
    assert parse_percent("10%") == 0.10
    assert parse_percent("fifteen percent (15%)") == 0.15


def test_parse_int_prefers_parenthesised_digits():
    assert parse_int("more than 6 visits") == 6
    assert parse_int("eighty (80)") == 80
    assert parse_int("two hundred and forty (240)") == 240


def test_half_up_cents_rounds_half_away_from_zero():
    # 151975 * 0.88 = 133738.0 exactly -- no rounding tension
    assert half_up_cents(151975, 0.88) == 133738
    # a genuine half-cent case: 5 * 0.5 = 2.5 -> rounds to 3 (half up), not 2
    assert half_up_cents(5, 0.5) == 3
