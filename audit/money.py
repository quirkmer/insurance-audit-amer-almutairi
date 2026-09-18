"""Money, percentage and rounding helpers shared by every contract reader.

All contracts state: amounts in whole cents, half-up rounding applied after
each individual step of a calculation (not once at the end). We use
``decimal.Decimal`` throughout so "half up" is exact, never a float artefact.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

_GBP_RE = re.compile(r"GBP\s*([\d,]+(?:\.\d+)?)")
_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_INT_RE = re.compile(r"(\d+(?:,\d+)*)")


def parse_gbp_to_cents(text: str) -> int:
    """'GBP 1,301.25' -> 130125. Raises if no GBP amount is found."""
    m = _GBP_RE.search(text)
    if not m:
        raise ValueError(f"no GBP amount found in {text!r}")
    amount = Decimal(m.group(1).replace(",", ""))
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def parse_percent(text: str) -> float:
    """'+20%' -> 0.20, '10%' -> 0.10, 'fifteen percent (15%)' -> 0.15."""
    m = _PCT_RE.search(text)
    if not m:
        raise ValueError(f"no percentage found in {text!r}")
    return float(m.group(1)) / 100.0


_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def parse_multiplier(text: str) -> float:
    """A bare decimal multiplier cell, e.g. '1', '1.1', '0.92' (hospital 5's
    facility/plan-tier tables) -- no GBP prefix, no percentage sign.
    """
    return float(text.strip())


def parse_int(text: str) -> int:
    """Extract the first integer quantity from text.

    Handles plain digits ('60 nights', '4 tests') and also the
    parenthesised digit form used in hospital 4's prose ('eighty (80)',
    'two hundred and forty (240)') by preferring the parenthesised digits
    when present, since they are unambiguous.
    """
    paren = re.search(r"\((\d+)\)", text)
    if paren:
        return int(paren.group(1))
    m = _INT_RE.search(text)
    if not m:
        raise ValueError(f"no integer found in {text!r}")
    return int(m.group(1).replace(",", ""))


def half_up_cents(amount_cents, multiplier) -> int:
    """Multiply an integer cent amount by a Decimal/float/int multiplier,
    rounding half-up to the nearest whole cent, per the contracts'
    'rounding applied after each individual step' rule.
    """
    d = Decimal(int(amount_cents)) * Decimal(str(multiplier))
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


UNIT_BASIS_MAP = {
    "per hour": ["per_hour"],
    "per visit": ["per_visit"],
    "per day of service": ["per_day"],
    "per night of occupancy": ["per_night"],
    "per item supplied": ["per_item"],
    "per test": ["per_test"],
    "per unit dispensed": ["per_unit_dispensed"],
    "per procedure": ["per_procedure"],
    # Hospital 3 / 4 both contain one service with a dual, ambiguous unit
    # basis ("per hour, per item"). Billing systems represent this as the
    # single combined code "per_hour_per_item" (confirmed against the
    # hospital 3 data); we also accept the plain "per_hour"/"per_item"
    # forms in case a hospital bills it either way.
    "per hour, per item": ["per_hour_per_item", "per_hour", "per_item"],
}


def normalize_unit_basis(contract_text: str) -> list[str]:
    key = contract_text.strip().lower()
    if key not in UNIT_BASIS_MAP:
        raise ValueError(f"unrecognised unit basis phrase: {contract_text!r}")
    return UNIT_BASIS_MAP[key]
