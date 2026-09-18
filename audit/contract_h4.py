"""Build a `Contract` for hospital 4 from conditional_reimbursement_agreement.md.

Single self-contained document, fully tabular -- the most mechanical of
the four contracts read for this exercise. Two shape differences from
hospital 1/3's tables, both confirmed by reading the actual headers
rather than assumed from familiarity:

* The base-rate table (Section 3) has no "Daily cap" column at all --
  caps live only in the separate Section 6 table.
* The bundle table (Section 7) interleaves rate with service name
  ("Service A | Substituted rate A | Service B | Substituted rate B"),
  not grouped as hospital 1/3 do ("Service A | Service B | Bundled rate
  A | Bundled rate B"). Same information, different column order --
  reading the header wrong here would silently swap which service gets
  which bundled rate.

Section 10 (Non-Business-Day Uplifts) is headed but contains "_None._"
with no table at all: this hospital has zero weekend-uplift services.
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
)
from audit.money import normalize_unit_basis, parse_gbp_to_cents, parse_int, parse_percent

CONTRACT_PATH = Path(__file__).parent.parent / "contracts" / "hospital_4" / "conditional_reimbursement_agreement.md"

TERM_START = dt.date(2024, 1, 1)
TERM_END = dt.date(2025, 12, 31)


def build_contract() -> Contract:
    tables = load_tables(CONTRACT_PATH)

    rate_table = find_table(tables, "Service", "Unit basis", "Base rate")
    rates = [
        RateEntry(
            service=row["Service"],
            unit_basis=tuple(normalize_unit_basis(row["Unit basis"])),
            rate_cents=parse_gbp_to_cents(row["Base rate"]),
            daily_cap=None,  # caps come from Section 6 below, not this table
            effective_from=TERM_START,
            effective_to=TERM_END,
        )
        for row in rate_table
    ]

    premium_table = find_table(tables, "Service", "Threshold (per Patient per Service Day)", "Uplift")
    premiums = [
        ThresholdPremium(
            service=row["Service"],
            daily_qty_threshold=parse_int(row["Threshold (per Patient per Service Day)"]),
            uplift=parse_percent(row["Uplift"]),
        )
        for row in premium_table
    ]

    cap_table = find_table(tables, "Service", "Maximum units per Patient per Service Day")
    caps = [
        DailyCap(row["Service"], parse_int(row["Maximum units per Patient per Service Day"]))
        for row in cap_table
    ]

    bundle_table = find_table(tables, "Service A", "Substituted rate A", "Service B", "Substituted rate B")
    bundles = [
        Bundle(
            service_a=row["Service A"],
            service_b=row["Service B"],
            rate_a_cents=parse_gbp_to_cents(row["Substituted rate A"]),
            rate_b_cents=parse_gbp_to_cents(row["Substituted rate B"]),
        )
        for row in bundle_table
    ]

    discount_table = find_table(tables, "Service", "Cumulative utilisation exceeds", "Discount on subsequent instances")
    discounts = [
        VolumeDiscount(
            service=row["Service"],
            cumulative_threshold=parse_int(row["Cumulative utilisation exceeds"]),
            discount=parse_percent(row["Discount on subsequent instances"]),
        )
        for row in discount_table
    ]

    exclusion_table = find_table(tables, "Service", "Window", "Excluded by delivery of")
    exclusions = [
        ExclusionWindow(
            service=row["Service"],
            window_days=parse_int(row["Window"]),
            excluded_by=row["Excluded by delivery of"],
        )
        for row in exclusion_table
    ]

    # Section 10 (Non-Business-Day Uplifts) has no table -- "_None._" --
    # so weekend_uplifts is deliberately empty for this hospital.

    return Contract(
        hospital_id="H4",
        contract_number="INS-H4-2024-2049",
        term_start=TERM_START,
        term_end=TERM_END,
        rates=rates,
        threshold_premiums=premiums,
        weekend_uplifts=[],
        volume_discounts=discounts,
        daily_caps=caps,
        bundles=bundles,
        exclusion_windows=exclusions,
    )
