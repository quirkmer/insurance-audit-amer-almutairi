"""Build a `Contract` for hospital 1 from provider_services_agreement.md.

Hospital 1 is the simplest layout: one document, one rate schedule, no
mid-term amendment. Section numbers below refer to that document.
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

CONTRACT_PATH = Path(__file__).parent.parent / "contracts" / "hospital_1" / "provider_services_agreement.md"

TERM_START = dt.date(2024, 1, 1)
TERM_END = dt.date(2025, 12, 31)


def build_contract() -> Contract:
    tables = load_tables(CONTRACT_PATH)

    rate_table = find_table(tables, "Service", "Unit basis", "Rate", "Daily cap")
    rates = []
    caps_from_rate_table = []
    for row in rate_table:
        cap = None
        if row["Daily cap"] != "—":
            cap = parse_int(row["Daily cap"])
            caps_from_rate_table.append(DailyCap(row["Service"], cap))
        # one RateEntry per rate row, carrying *every* accepted billed unit
        # basis together (a row can legitimately accept more than one, e.g.
        # "per hour, per item") -- splitting these into separate single-basis
        # entries would make rate_entry() pick an arbitrary one and then
        # incorrectly flag the other accepted basis as "wrong".
        rates.append(
            RateEntry(
                service=row["Service"],
                unit_basis=tuple(normalize_unit_basis(row["Unit basis"])),
                rate_cents=parse_gbp_to_cents(row["Rate"]),
                daily_cap=cap,
                effective_from=TERM_START,
                effective_to=TERM_END,
            )
        )

    premium_table = find_table(tables, "Service", "Applies when daily quantity exceeds", "Uplift")
    premiums = [
        ThresholdPremium(
            service=row["Service"],
            daily_qty_threshold=parse_int(row["Applies when daily quantity exceeds"]),
            uplift=parse_percent(row["Uplift"]),
        )
        for row in premium_table
    ]

    weekend_table = find_table(tables, "Service", "Uplift where the Service Date is not a Business Day")
    weekend = [
        WeekendUplift(
            service=row["Service"],
            uplift=parse_percent(row["Uplift where the Service Date is not a Business Day"]),
        )
        for row in weekend_table
    ]

    discount_table = find_table(tables, "Service", "Cumulative utilisation exceeds", "Discount on subsequent units")
    discounts = [
        VolumeDiscount(
            service=row["Service"],
            cumulative_threshold=parse_int(row["Cumulative utilisation exceeds"]),
            discount=parse_percent(row["Discount on subsequent units"]),
        )
        for row in discount_table
    ]

    cap_table = find_table(tables, "Service", "Maximum billable units per Patient per Service Day")
    caps = list(caps_from_rate_table) + [
        DailyCap(row["Service"], parse_int(row["Maximum billable units per Patient per Service Day"]))
        for row in cap_table
    ]
    # de-duplicate service->cap (rate table and Section 8 restate the same figure)
    seen = {}
    for c in caps:
        seen[c.service] = c.max_qty
    caps = [DailyCap(s, q) for s, q in seen.items()]

    bundle_table = find_table(tables, "Service A", "Service B", "Bundled rate A", "Bundled rate B")
    bundles = [
        Bundle(
            service_a=row["Service A"],
            service_b=row["Service B"],
            rate_a_cents=parse_gbp_to_cents(row["Bundled rate A"]),
            rate_b_cents=parse_gbp_to_cents(row["Bundled rate B"]),
        )
        for row in bundle_table
    ]

    exclusion_table = find_table(tables, "Service", "Not billable within", "Of this Service")
    exclusions = [
        ExclusionWindow(
            service=row["Service"],
            window_days=parse_int(row["Not billable within"]),
            excluded_by=row["Of this Service"],
        )
        for row in exclusion_table
    ]

    return Contract(
        hospital_id="H1",
        contract_number="INS-H1-2024-0417",
        term_start=TERM_START,
        term_end=TERM_END,
        rates=rates,
        threshold_premiums=premiums,
        weekend_uplifts=weekend,
        volume_discounts=discounts,
        daily_caps=caps,
        bundles=bundles,
        exclusion_windows=exclusions,
    )
