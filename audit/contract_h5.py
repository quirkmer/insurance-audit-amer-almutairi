"""Build a `Contract` for hospital 5 from network_reimbursement_agreement.md.

The one contract of the five where facility and plan-tier multipliers are
*not* a no-op: rates vary by which of three facilities (F-MAIN, F-NORTH,
F-COAST) delivered the service, and by the patient's plan tier (BRONZE,
SILVER, GOLD), via two separate multiplier tables (Table 2, Table 3)
applied in that order, before any premium and after any bundle
substitution -- same adjustment order as every other hospital, just with
two more real steps in it instead of two no-ops. This is why
`audit/model.py` and `audit/pricing.py` carry explicit facility/plan-tier
multiplier support rather than the "always 1.0" shortcut the other four
hospitals could get away with.

Everything else (base rates, threshold premiums, weekend uplifts, the
interleaved-column bundle table also seen in hospital 4, cumulative
discounts, exclusion windows) follows the same shapes already handled
elsewhere.
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
    FacilityMultiplier,
    PlanTierMultiplier,
    RateEntry,
    ThresholdPremium,
    VolumeDiscount,
    WeekendUplift,
)
from audit.money import normalize_unit_basis, parse_gbp_to_cents, parse_int, parse_multiplier, parse_percent

CONTRACT_PATH = Path(__file__).parent.parent / "contracts" / "hospital_5" / "network_reimbursement_agreement.md"

TERM_START = dt.date(2024, 1, 1)
TERM_END = dt.date(2025, 12, 31)

FACILITY_CODES = ["F-MAIN", "F-NORTH", "F-COAST"]
PLAN_TIERS = ["BRONZE", "SILVER", "GOLD"]


def build_contract() -> Contract:
    tables = load_tables(CONTRACT_PATH)

    rate_table = find_table(tables, "Service", "Unit basis", "Base rate", "Daily cap")
    rates = []
    caps_from_rate_table = []
    for row in rate_table:
        cap = None
        if row["Daily cap"] != "—":
            cap = parse_int(row["Daily cap"])
            caps_from_rate_table.append(DailyCap(row["Service"], cap))
        rates.append(
            RateEntry(
                service=row["Service"],
                unit_basis=tuple(normalize_unit_basis(row["Unit basis"])),
                rate_cents=parse_gbp_to_cents(row["Base rate"]),
                daily_cap=cap,
                effective_from=TERM_START,
                effective_to=TERM_END,
            )
        )

    facility_table = find_table(tables, "Service", *FACILITY_CODES)
    facility_multipliers = [
        FacilityMultiplier(row["Service"], code, parse_multiplier(row[code]))
        for row in facility_table
        for code in FACILITY_CODES
    ]

    tier_table = find_table(tables, "Service", *PLAN_TIERS)
    plan_tier_multipliers = [
        PlanTierMultiplier(row["Service"], tier, parse_multiplier(row[tier]))
        for row in tier_table
        for tier in PLAN_TIERS
    ]

    premium_table = find_table(tables, "Service", "Daily quantity threshold", "Uplift")
    premiums = [
        ThresholdPremium(row["Service"], parse_int(row["Daily quantity threshold"]), parse_percent(row["Uplift"]))
        for row in premium_table
    ]

    weekend_table = find_table(tables, "Service", "Uplift")
    weekend = [WeekendUplift(row["Service"], parse_percent(row["Uplift"])) for row in weekend_table]

    bundle_table = find_table(tables, "Service A", "Substituted rate A", "Service B", "Substituted rate B")
    bundles = [
        Bundle(row["Service A"], row["Service B"], parse_gbp_to_cents(row["Substituted rate A"]), parse_gbp_to_cents(row["Substituted rate B"]))
        for row in bundle_table
    ]

    discount_table = find_table(tables, "Service", "Cumulative utilisation", "Discount on subsequent units")
    discounts = [
        VolumeDiscount(row["Service"], parse_int(row["Cumulative utilisation"]), parse_percent(row["Discount on subsequent units"]))
        for row in discount_table
    ]

    exclusion_table = find_table(tables, "Service", "Not billable within", "Of this Service")
    exclusions = [
        ExclusionWindow(row["Service"], parse_int(row["Not billable within"]), row["Of this Service"])
        for row in exclusion_table
    ]

    # de-duplicate caps: the base-rate table and Section-style cap tables
    # never disagree in the source contracts, but keep one source of truth
    cap_map = {c.service: c.max_qty for c in caps_from_rate_table}
    daily_caps = [DailyCap(s, q) for s, q in cap_map.items()]

    return Contract(
        hospital_id="H5",
        contract_number="INS-H5-2024-0731",
        term_start=TERM_START,
        term_end=TERM_END,
        rates=rates,
        threshold_premiums=premiums,
        weekend_uplifts=weekend,
        volume_discounts=discounts,
        daily_caps=daily_caps,
        bundles=bundles,
        exclusion_windows=exclusions,
        facility_multipliers=facility_multipliers,
        plan_tier_multipliers=plan_tier_multipliers,
    )
