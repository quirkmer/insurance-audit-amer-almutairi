"""Build a `Contract` for hospital 3 from its three documents.

Hospital 3 is the "amendment repriced things mid-term" contract: Appendix B
carries the rates as originally agreed; Amendment No. 1 substitutes seven
of those rates and adds two brand-new services, all effective by
*service date* from 1 January 2025 (base_agreement.md clause A1.1.2 in the
amendment: the invoice date is irrelevant). Everything else -- premiums,
weekend uplifts, volume discounts, caps, bundles, exclusion windows -- is
defined once in the Base Agreement and is unaffected by the amendment
(clause A1.4.1), so it applies identically across the whole term.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from audit.md_tables import find_table, load_tables
from audit.model import (
    Bundle,
    Contract,
    DailyCap,
    ExclusionWindow,
    RateEntry,
    ThresholdPremium,
    VolumeDiscount,
    WeekendUplift,
)
from audit.money import normalize_unit_basis, parse_gbp_to_cents, parse_int, parse_percent

BASE_PATH = Path(__file__).parent.parent / "contracts" / "hospital_3" / "base_agreement.md"
APPENDIX_PATH = Path(__file__).parent.parent / "contracts" / "hospital_3" / "appendix_b_rate_schedule.md"
AMENDMENT_PATH = Path(__file__).parent.parent / "contracts" / "hospital_3" / "amendment_no_1.md"

TERM_START = dt.date(2024, 1, 1)
TERM_END = dt.date(2025, 12, 31)
AMENDMENT_EFFECTIVE = dt.date(2025, 1, 1)


def build_contract() -> Contract:
    base_tables = load_tables(BASE_PATH)
    appendix_tables = load_tables(APPENDIX_PATH)
    amendment_tables = load_tables(AMENDMENT_PATH)

    # --- Appendix B: rates as originally agreed ---
    appendix_rate_table = find_table(appendix_tables, "Service", "Unit basis", "Rate", "Daily cap")
    appendix_rates: dict[str, tuple[tuple[str, ...], int, int | None]] = {}
    caps: dict[str, int] = {}
    for row in appendix_rate_table:
        cap = None if row["Daily cap"] == "—" else parse_int(row["Daily cap"])
        basis = tuple(normalize_unit_basis(row["Unit basis"]))
        appendix_rates[row["Service"]] = (basis, parse_gbp_to_cents(row["Rate"]), cap)
        if cap is not None:
            caps[row["Service"]] = cap

    # --- Amendment No. 1: substituted rates (by service date, not invoice date) ---
    sub_table = find_table(
        amendment_tables, "Service", "Unit basis", "Rate to 31 December 2024", "Rate from 1 January 2025"
    )
    substituted_services = {row["Service"] for row in sub_table}

    # One RateEntry per row/window, carrying *every* accepted unit basis
    # together -- see the equivalent note in contract_h1.py. Splitting a
    # dual-basis service ("per hour, per item") into separate single-basis
    # entries would make rate_entry() return an arbitrary one and then
    # incorrectly flag the other accepted basis as "wrong".
    rates: list[RateEntry] = []
    for service, (basis, rate_cents, cap) in appendix_rates.items():
        if service in substituted_services:
            continue  # replaced below with a split pre/post-amendment entry
        rates.append(RateEntry(service, basis, rate_cents, cap, TERM_START, TERM_END))

    for row in sub_table:
        basis = tuple(normalize_unit_basis(row["Unit basis"]))
        cap = caps.get(row["Service"])  # caps are untouched by the amendment
        rate_2024 = parse_gbp_to_cents(row["Rate to 31 December 2024"])
        rate_2025 = parse_gbp_to_cents(row["Rate from 1 January 2025"])
        rates.append(RateEntry(row["Service"], basis, rate_2024, cap, TERM_START, AMENDMENT_EFFECTIVE - dt.timedelta(days=1)))
        rates.append(RateEntry(row["Service"], basis, rate_2025, cap, AMENDMENT_EFFECTIVE, TERM_END))

    # --- Amendment No. 1: brand-new services, billable only from 2025-01-01 ---
    additional_table = find_table(amendment_tables, "Service", "Unit basis", "Rate")
    for row in additional_table:
        basis = tuple(normalize_unit_basis(row["Unit basis"]))
        rate_cents = parse_gbp_to_cents(row["Rate"])
        rates.append(RateEntry(row["Service"], basis, rate_cents, None, AMENDMENT_EFFECTIVE, TERM_END))

    # --- Base Agreement: premiums, weekend uplifts, discounts, caps, bundles, exclusions ---
    premium_table = find_table(base_tables, "Service", "Daily quantity threshold", "Uplift")
    premiums = [
        ThresholdPremium(row["Service"], parse_int(row["Daily quantity threshold"]), parse_percent(row["Uplift"]))
        for row in premium_table
    ]

    weekend_table = find_table(base_tables, "Service", "Uplift")
    weekend = [WeekendUplift(row["Service"], parse_percent(row["Uplift"])) for row in weekend_table]

    discount_table = find_table(base_tables, "Service", "Cumulative utilisation", "Discount")
    discounts = [
        VolumeDiscount(row["Service"], parse_int(row["Cumulative utilisation"]), parse_percent(row["Discount"]))
        for row in discount_table
    ]

    cap_table = find_table(base_tables, "Service", "Maximum units per Patient per Service Day")
    for row in cap_table:
        caps[row["Service"]] = parse_int(row["Maximum units per Patient per Service Day"])
    daily_caps = [DailyCap(s, q) for s, q in caps.items()]

    bundle_table = find_table(base_tables, "Service A", "Service B", "Bundled rate A", "Bundled rate B")
    bundles = [
        Bundle(row["Service A"], row["Service B"], parse_gbp_to_cents(row["Bundled rate A"]), parse_gbp_to_cents(row["Bundled rate B"]))
        for row in bundle_table
    ]

    exclusion_table = find_table(base_tables, "Service", "Not billable within", "Of this Service")
    exclusions = [
        ExclusionWindow(row["Service"], parse_int(row["Not billable within"]), row["Of this Service"])
        for row in exclusion_table
    ]

    return Contract(
        hospital_id="H3",
        contract_number="INS-H3-2024-0562",
        term_start=TERM_START,
        term_end=TERM_END,
        rates=rates,
        threshold_premiums=premiums,
        weekend_uplifts=weekend,
        volume_discounts=discounts,
        daily_caps=daily_caps,
        bundles=bundles,
        exclusion_windows=exclusions,
    )
