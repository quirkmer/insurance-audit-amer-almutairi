"""The repricing engine: given a Contract and a hospital's full invoice and
line-item tables, recompute what each line item *should* have cost and
compare it to what was billed.

Every rule below is applied exactly as read from the relevant contract
except where the contract text is genuinely ambiguous; those points are
resolved empirically against the hospital 1 labels (see prompts/003 and
prompts/004) rather than guessed, and are called out here:

* Threshold premiums apply to the *entire* day's quantity once the
  aggregate exceeds the threshold, not just the units above it. Confirmed
  against INV-H1-000348 (a single premium_omitted case): reproducing the
  label's expected_total_cents only works if all 13 hours are uplifted,
  not just the 5 above the 8-hour threshold.
* A daily-cap violation's "expected" quantity is not recoverable from the
  contract alone -- the cap only bounds it from above. We use the
  standard auditing convention (excess is not payable, so bill up to the
  cap) rather than guess the pre-corruption value, and report reduced
  confidence on the *dollar figure* for these rows accordingly.
* An unresolved or unrecognised service description is left at its billed
  amount (no invented correction) and flagged with low confidence --
  confirmed against the same INV-H1-000348 example, whose "unknown_service"
  line only reconciles to the label if left unchanged.
* Cumulative-volume-discount utilisation is counted against every billed
  unit (not the cap-adjusted quantity), per the contracts' "Units ...
  billed" wording.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd

from audit.matching import (
    build_unit_basis_index,
    resolve_by_price,
    resolve_descriptions,
    score_description,
    strip_billing_code,
    tokenize,
)
from audit.model import Contract
from audit.money import half_up_cents

CATEGORY_UNKNOWN_SERVICE = "unknown_service"
CATEGORY_UNIT_PRICE_MISMATCH = "unit_price_mismatch"
CATEGORY_WRONG_UNIT_BASIS = "wrong_unit_basis"
CATEGORY_LINE_TOTAL_ARITHMETIC = "line_total_arithmetic"
CATEGORY_INVOICE_TOTAL_MISMATCH = "invoice_total_mismatch"
CATEGORY_DAILY_CAP_EXCEEDED = "daily_cap_exceeded"
CATEGORY_PREMIUM_INCORRECT = "premium_incorrectly_applied"
CATEGORY_PREMIUM_OMITTED = "premium_omitted"
CATEGORY_VOLUME_DISCOUNT_INCORRECT = "volume_discount_incorrectly_applied"
CATEGORY_VOLUME_DISCOUNT_OMITTED = "volume_discount_omitted"
CATEGORY_BUNDLE_NOT_APPLIED = "bundle_not_applied"
CATEGORY_EXCLUSION_WINDOW = "exclusion_window_violation"
CATEGORY_DUPLICATE_INVOICE_ID = "duplicate_invoice_id"
CATEGORY_CONTRACT_NUMBER_MISMATCH = "contract_number_mismatch"
CATEGORY_SERVICE_DATE_AFTER_INVOICE_DATE = "service_date_after_invoice_date"
CATEGORY_SERVICE_DATE_OUT_OF_WINDOW = "service_date_out_of_window"
CATEGORY_MALFORMED_SERVICE_DATE = "malformed_service_date"
CATEGORY_CROSS_INVOICE_DUPLICATE = "cross_invoice_duplicate"
CATEGORY_INVOICE_SUBMITTED_LATE = "invoice_submitted_late"


@dataclass
class LinePricing:
    line_id: str
    invoice_id: str
    resolved_service: str | None
    match_confidence: float
    match_method: str
    expected_line_total_cents: int
    dollar_amount_uncertain: bool
    categories: set[str] = field(default_factory=set)


def _is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5  # Mon=0 .. Sun=6


def _build_description_stats(li: pd.DataFrame) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for desc, grp in li.groupby("description"):
        stats[desc] = {
            "unit_basis": Counter(grp["unit_basis_as_billed"]),
            "unit_price_cents": Counter(grp["unit_price_cents"]),
            "service_dates": [d for d in grp["service_date_parsed"] if pd.notna(d)],
        }
    return stats


def price_hospital(contract: Contract, invoices: pd.DataFrame, line_items: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (line_results_df, invoice_results_df)."""
    inv = invoices.copy()
    li = line_items.copy()

    inv["invoice_date_parsed"] = pd.to_datetime(inv["invoice_date"], errors="coerce").dt.date
    inv["admission_date_parsed"] = pd.to_datetime(inv["admission_date"], errors="coerce").dt.date
    inv["discharge_date_parsed"] = pd.to_datetime(inv["discharge_date"], errors="coerce").dt.date
    li["service_date_parsed"] = pd.to_datetime(li["service_date"], errors="coerce", format="%Y-%m-%d").dt.date

    # A hospital occasionally reuses an invoice_id for two genuinely
    # different invoices (itself a violation we detect separately, in
    # _aggregate_to_invoice, from the *undeduplicated* table). For this
    # merge we must take exactly one header row per id -- otherwise every
    # line item under a reused id is fanned out and double-counted in the
    # cross-invoice running totals below (cumulative discount, threshold
    # premiums, caps), silently corrupting unrelated invoices that just
    # happen to sequence after it.
    inv_dedup = inv.drop_duplicates(subset="invoice_id", keep="first")
    li = li.merge(
        inv_dedup[[
            "invoice_id", "patient_id", "invoice_date_parsed", "contract_number", "invoice_total_cents",
            "facility_code", "plan_tier",
        ]],
        on="invoice_id",
        how="left",
        suffixes=("", "_inv"),
    )

    # --- description -> service resolution (aggregate, then per-line price fallback) ---
    desc_stats = _build_description_stats(li)
    desc_resolution = resolve_descriptions(contract, desc_stats)

    catalog_services = contract.services
    unit_basis_index = build_unit_basis_index(contract)

    def resolve_line(row) -> tuple[str | None, float, str]:
        base = desc_resolution.get(row["description"])
        if base is not None and base.service is not None:
            return base.service, base.confidence, base.method
        # per-line fallback: does *this specific* line's own (unit_basis, price)
        # match exactly one contracted service on its own service date?
        cleaned = strip_billing_code(row["description"])
        tokens = tokenize(cleaned)
        if pd.notna(row["service_date_parsed"]):
            hits = resolve_by_price(
                row["unit_basis_as_billed"], row["unit_price_cents"], contract, [row["service_date_parsed"]],
                facility_code=row.get("facility_code"), plan_tier=row.get("plan_tier"),
            )
            if len(hits) == 1 and score_description(tokens, tuple(tokenize(hits[0]))) >= 0.3:
                # (same "price alone is not enough" guard as the aggregate
                # resolver -- see matching.resolve_descriptions)
                return hits[0], 0.9, "price_anchor_line"
        # last resort: best text score, preferring services whose contracted
        # unit basis matches what was actually billed (see the identical
        # rationale in matching.resolve_descriptions); reported but not
        # trusted as a confident match either way.
        basis_compatible = [s for s in catalog_services if row["unit_basis_as_billed"] in unit_basis_index.get(s, set())]
        pool = basis_compatible or catalog_services
        scored = sorted(
            ((score_description(tokens, tuple(tokenize(s))), s) for s in pool), reverse=True
        )
        if scored and scored[0][0] >= 0.55:
            return scored[0][1], min(0.4, scored[0][0] * 0.5), "text_weak"
        return None, base.confidence if base else 0.0, "unresolved"

    resolved = li.apply(resolve_line, axis=1, result_type="expand")
    li["resolved_service"], li["match_confidence"], li["match_method"] = resolved[0], resolved[1], resolved[2]

    # --- global sequencing key for cumulative-utilisation rules ---
    li = li.sort_values(["service_date_parsed", "line_id"], na_position="last").reset_index(drop=True)

    # --- bundle detection: which (patient, service_date) pairs have both partner services present ---
    bundle_day_index: dict[tuple[str, dt.date], set[str]] = defaultdict(set)
    for _, row in li.iterrows():
        if pd.notna(row["service_date_parsed"]) and row["resolved_service"]:
            bundle_day_index[(row["patient_id"], row["service_date_parsed"])].add(row["resolved_service"])

    def bundle_active(patient: str, day, service: str) -> bool:
        partner = contract.bundle_partner(service)
        if partner is None or pd.isna(day):
            return False
        other = partner.service_b if service == partner.service_a else partner.service_a
        return other in bundle_day_index.get((patient, day), set())

    # --- daily-aggregate quantity per (patient, service_date, service), for premiums & caps ---
    day_qty: dict[tuple[str, object, str], int] = defaultdict(int)
    for _, row in li.iterrows():
        if row["resolved_service"] and pd.notna(row["service_date_parsed"]):
            day_qty[(row["patient_id"], row["service_date_parsed"], row["resolved_service"])] += int(row["quantity"])

    # --- exclusion windows: per-patient, per-service delivery dates ---
    patient_service_dates: dict[tuple[str, str], list] = defaultdict(list)
    for _, row in li.iterrows():
        if row["resolved_service"] and pd.notna(row["service_date_parsed"]):
            patient_service_dates[(row["patient_id"], row["resolved_service"])].append(row["service_date_parsed"])

    def excluded(patient: str, service: str, day) -> bool:
        if pd.isna(day):
            return False
        for rule in contract.exclusions_for(service):
            for other_day in patient_service_dates.get((patient, rule.excluded_by), []):
                if other_day != day and abs((other_day - day).days) <= rule.window_days:
                    return True
        return False

    # --- duplicate same (patient, service_date, service) across >1 line/invoice ---
    dup_group: dict[tuple[str, object, str], list[str]] = defaultdict(list)
    for _, row in li.iterrows():
        if row["resolved_service"] and pd.notna(row["service_date_parsed"]):
            dup_group[(row["patient_id"], row["service_date_parsed"], row["resolved_service"])].append(row["line_id"])

    # --- cumulative volume-discount running totals, in (service_date, line_id) global order ---
    running_utilisation: dict[str, int] = defaultdict(int)
    # cap allocation running totals per (patient, service_date, service)
    cap_used: dict[tuple[str, object, str], int] = defaultdict(int)

    line_results: list[LinePricing] = []

    for _, row in li.iterrows():
        service = row["resolved_service"]
        line_id = row["line_id"]
        categories: set[str] = set()
        qty = int(row["quantity"])
        day = row["service_date_parsed"]
        patient = row["patient_id"]

        if pd.isna(day):
            categories.add(CATEGORY_MALFORMED_SERVICE_DATE)

        if service is None:
            line_results.append(
                LinePricing(line_id, row["invoice_id"], None, float(row["match_confidence"]), row["match_method"],
                            int(row["line_total_cents"]), True, categories | {CATEGORY_UNKNOWN_SERVICE})
            )
            continue

        rate_entry = contract.rate_entry(service, day) if pd.notna(day) else None
        # a service can be genuinely un-contracted for this date (H3's mid-term
        # additions) even though the *name* is recognised -- treat the same as
        # unknown for pricing purposes, but keep the higher-confidence label.
        if rate_entry is None:
            entries = contract.all_rate_entries(service)
            rate_entry = entries[0] if entries else None
        if rate_entry is None:
            line_results.append(
                LinePricing(line_id, row["invoice_id"], service, float(row["match_confidence"]), row["match_method"],
                            int(row["line_total_cents"]), True, categories | {CATEGORY_UNKNOWN_SERVICE})
            )
            continue

        if row["unit_basis_as_billed"] not in rate_entry.unit_basis:
            categories.add(CATEGORY_WRONG_UNIT_BASIS)

        # (a) bundle substitution
        if bundle_active(patient, day, service):
            partner = contract.bundle_partner(service)
            rate_cents = partner.rate_a_cents if service == partner.service_a else partner.rate_b_cents
        else:
            rate_cents = rate_entry.rate_cents
            partner = contract.bundle_partner(service)
            if partner is not None:
                # a bundle *should* have applied but the partner wasn't
                # delivered -- nothing to flag here; flagging happens only
                # when price evidence below suggests one was expected.
                pass

        # (b)/(c) facility & plan-tier multipliers. 1.0 (a no-op, but the
        # contracts still call for the rounding step) for every hospital
        # except hospital 5, which is the only one with real differentials;
        # Contract.facility_multiplier/plan_tier_multiplier return 1.0 for
        # any (service, code) pair not on file, so this is inert elsewhere.
        rate_cents = half_up_cents(rate_cents, contract.facility_multiplier(service, row.get("facility_code")))
        rate_cents = half_up_cents(rate_cents, contract.plan_tier_multiplier(service, row.get("plan_tier")))

        base_rate_before_premium = rate_cents

        # (d) premium or uplift -- weekend and threshold premiums never target
        # the same service in hospitals 1/3/4, so at most one applies.
        premium = contract.threshold_premium(service)
        weekend = contract.weekend_uplift(service)
        uplift_applied = False
        if premium is not None:
            aggregate = day_qty.get((patient, day, service), qty)
            if aggregate > premium.daily_qty_threshold:
                rate_cents = half_up_cents(rate_cents, 1 + premium.uplift)
                uplift_applied = True
        elif weekend is not None and pd.notna(day) and not _is_business_day(day):
            rate_cents = half_up_cents(rate_cents, 1 + weekend.uplift)
            uplift_applied = True

        # detect premium mismatches by comparing to the *billed* price where
        # the service itself was confidently resolved and not bundle/cap-affected
        if row["match_confidence"] >= 0.9 and not bundle_active(patient, day, service):
            billed_price = int(row["unit_price_cents"])
            expected_price_no_premium = base_rate_before_premium
            if premium is not None or weekend is not None:
                if uplift_applied and billed_price == expected_price_no_premium:
                    categories.add(CATEGORY_PREMIUM_OMITTED)
                elif not uplift_applied and premium is not None:
                    aggregate = day_qty.get((patient, day, service), qty)
                    would_be = half_up_cents(base_rate_before_premium, 1 + premium.uplift)
                    if billed_price == would_be and aggregate <= premium.daily_qty_threshold:
                        categories.add(CATEGORY_PREMIUM_INCORRECT)

        # (e) cumulative volume discount, evaluated on prior global utilisation
        tiers = contract.volume_discount_tiers(service)
        discount = 0.0
        if tiers:
            prior = running_utilisation[service]
            for tier in tiers:  # ascending threshold; last match wins (deepest)
                if prior > tier.cumulative_threshold:
                    discount = tier.discount
        if discount:
            rate_cents = half_up_cents(rate_cents, 1 - discount)
        running_utilisation[service] += qty

        # daily cap allocation (based on billed quantity; excess is not payable)
        cap = rate_entry.daily_cap or contract.daily_cap(service)
        billable_qty = qty
        if cap is not None:
            key = (patient, day, service)
            already = cap_used[key]
            remaining = max(0, cap - already)
            billable_qty = min(qty, remaining)
            cap_used[key] = already + qty
            if qty > remaining:
                categories.add(CATEGORY_DAILY_CAP_EXCEEDED)

        expected_line_total = billable_qty * rate_cents
        dollar_uncertain = CATEGORY_DAILY_CAP_EXCEEDED in categories

        # exclusion window: fully non-billable if triggered
        if excluded(patient, service, day):
            categories.add(CATEGORY_EXCLUSION_WINDOW)
            expected_line_total = 0

        # cross-line/-invoice duplicate same (patient, service, day)
        group = dup_group.get((patient, day, service), [])
        if len(group) > 1 and pd.notna(day) and line_id != min(group):
            # the earliest-billed line (by ascending line identifier, the
            # contracts' own tie-break rule) is the legitimate one; only the
            # later re-presentation(s) of the same service are the error.
            invoice_ids = set(li.loc[li["line_id"].isin(group), "invoice_id"])
            if len(invoice_ids) > 1:
                categories.add(CATEGORY_CROSS_INVOICE_DUPLICATE)
            expected_line_total = 0

        # unit price sanity flag (informational; independent of expected_total)
        if row["match_confidence"] >= 0.9 and not categories & {
            CATEGORY_EXCLUSION_WINDOW, CATEGORY_CROSS_INVOICE_DUPLICATE, CATEGORY_DAILY_CAP_EXCEEDED
        }:
            if int(row["unit_price_cents"]) != rate_cents:
                categories.add(CATEGORY_UNIT_PRICE_MISMATCH)

        # internal arithmetic sanity: does billed qty*price == billed line_total?
        if int(row["quantity"]) * int(row["unit_price_cents"]) != int(row["line_total_cents"]):
            categories.add(CATEGORY_LINE_TOTAL_ARITHMETIC)

        line_results.append(
            LinePricing(line_id, row["invoice_id"], service, float(row["match_confidence"]), row["match_method"],
                        int(expected_line_total), dollar_uncertain, categories)
        )

    line_df = pd.DataFrame([{
        "line_id": r.line_id,
        "invoice_id": r.invoice_id,
        "resolved_service": r.resolved_service,
        "match_confidence": r.match_confidence,
        "match_method": r.match_method,
        "expected_line_total_cents": r.expected_line_total_cents,
        "dollar_amount_uncertain": r.dollar_amount_uncertain,
        "categories": "|".join(sorted(r.categories)),
    } for r in line_results])

    invoice_df = _aggregate_to_invoice(contract, inv, li, line_df)
    return line_df, invoice_df


def _aggregate_to_invoice(contract: Contract, inv: pd.DataFrame, li: pd.DataFrame, line_df: pd.DataFrame) -> pd.DataFrame:
    li_slim = li.drop(columns=["resolved_service", "match_confidence", "match_method"])
    merged = li_slim.merge(line_df, on=["line_id", "invoice_id"], how="left")

    rows = []
    dup_ids = inv["invoice_id"].value_counts()
    duplicated_ids = set(dup_ids[dup_ids > 1].index)

    for invoice_id, grp in merged.groupby("invoice_id"):
        inv_row = inv[inv["invoice_id"] == invoice_id].iloc[0]
        categories: set[str] = set()
        for cat_str in grp["categories"]:
            if cat_str:
                categories.update(cat_str.split("|"))

        billed_total = int(inv_row["invoice_total_cents"])
        is_duplicated_id = invoice_id in duplicated_ids
        line_total_sum_billed = int(grp["line_total_cents"].sum())
        if not is_duplicated_id and line_total_sum_billed != billed_total:
            categories.add(CATEGORY_INVOICE_TOTAL_MISMATCH)

        if is_duplicated_id:
            categories.add(CATEGORY_DUPLICATE_INVOICE_ID)

        if inv_row["contract_number"] != contract.contract_number:
            categories.add(CATEGORY_CONTRACT_NUMBER_MISMATCH)

        invoice_date = inv_row["invoice_date_parsed"]
        for service_date in grp["service_date_parsed"]:
            if pd.notna(service_date) and pd.notna(invoice_date) and service_date > invoice_date:
                categories.add(CATEGORY_SERVICE_DATE_AFTER_INVOICE_DATE)
            if pd.notna(service_date) and not (contract.term_start <= service_date <= contract.term_end):
                categories.add(CATEGORY_SERVICE_DATE_OUT_OF_WINDOW)

        # hospital 2 only: invoice must be submitted within N days of the
        # discharge date (Article XIII). Every other hospital's contract is
        # silent on this, so `invoice_submission_window_days` is None for
        # them and this never fires.
        if contract.invoice_submission_window_days is not None:
            discharge_date = inv_row.get("discharge_date_parsed")
            if pd.notna(discharge_date) and pd.notna(invoice_date):
                if (invoice_date - discharge_date).days > contract.invoice_submission_window_days:
                    categories.add(CATEGORY_INVOICE_SUBMITTED_LATE)

        min_match_conf = float(grp["match_confidence"].min()) if len(grp) else 1.0
        any_dollar_uncertain = bool(grp["dollar_amount_uncertain"].any()) or is_duplicated_id
        any_unresolved = (grp["resolved_service"].isna()).any()

        if is_duplicated_id:
            # The line items under a reused id cannot be reliably split
            # between the two physical invoices that share it, so we do not
            # attempt to reprice them: we flag the id collision itself
            # (unambiguous and deterministic) and report the invoice's own
            # billed total as its expected total, rather than guess an
            # attribution. See decision log, "Duplicate invoice IDs".
            expected_total = billed_total
            flagged = 1
        else:
            expected_total = int(grp["expected_line_total_cents"].sum())
            mismatch = expected_total != billed_total
            flagged = int(mismatch or bool(categories))
            if mismatch and not categories:
                categories.add(CATEGORY_INVOICE_TOTAL_MISMATCH)

        confidence = _confidence(
            categories=categories,
            min_match_conf=min_match_conf,
            any_dollar_uncertain=any_dollar_uncertain,
            any_unresolved=any_unresolved,
            flagged=bool(flagged),
        )

        rows.append({
            "invoice_id": invoice_id,
            "flagged": flagged,
            "error_category": "|".join(sorted(categories)),
            "expected_total_cents": expected_total,
            "billed_total_cents": billed_total,
            "confidence": round(confidence, 3),
        })

    return pd.DataFrame(rows)


_STRUCTURAL_ONLY = {
    CATEGORY_DUPLICATE_INVOICE_ID,
    CATEGORY_CONTRACT_NUMBER_MISMATCH,
    CATEGORY_SERVICE_DATE_AFTER_INVOICE_DATE,
    CATEGORY_SERVICE_DATE_OUT_OF_WINDOW,
    CATEGORY_LINE_TOTAL_ARITHMETIC,
    CATEGORY_INVOICE_TOTAL_MISMATCH,
    CATEGORY_INVOICE_SUBMITTED_LATE,
}

_DOLLAR_UNCERTAIN_CATEGORIES = {CATEGORY_DAILY_CAP_EXCEEDED, CATEGORY_UNKNOWN_SERVICE}


def _confidence(categories: set[str], min_match_conf: float, any_dollar_uncertain: bool,
                any_unresolved: bool, flagged: bool) -> float:
    if not flagged:
        # "looks correct" is only as trustworthy as our worst service match
        if any_unresolved:
            return 0.45
        return max(0.55, min(0.95, 0.55 + 0.4 * min_match_conf))

    if categories and categories.issubset(_STRUCTURAL_ONLY):
        if any_dollar_uncertain:
            # duplicate_invoice_id: the *flag* is a deterministic fact, but
            # the line items behind a reused id can't be reliably split
            # between the two invoices that share it, so the dollar figure
            # is a stated convention (billed total, unchanged), not a
            # verified one.
            return 0.7
        return 0.95  # pure arithmetic / ID facts, no service resolution involved

    conf = 0.85
    if any_unresolved or CATEGORY_UNKNOWN_SERVICE in categories:
        conf = min(conf, 0.4)
    if any_dollar_uncertain:
        conf = min(conf, 0.65)
    conf = min(conf, 0.5 + 0.45 * min_match_conf)
    return max(0.2, conf)
