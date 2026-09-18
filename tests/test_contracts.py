import datetime as dt

from audit.contract_h1 import build_contract as build_h1
from audit.contract_h2 import build_contract as build_h2
from audit.contract_h3 import build_contract as build_h3
from audit.contract_h4 import build_contract as build_h4
from audit.contract_h5 import build_contract as build_h5


def test_h1_rate_schedule_size():
    c = build_h1()
    assert len(c.services) == 108
    assert c.rate_entry("Advanced Cardiac Recovery Room Occupancy", dt.date(2024, 3, 1)).rate_cents == 20000


def test_h3_amendment_splits_rate_by_service_date():
    c = build_h3()
    pre = c.rate_entry("Ambulatory Otolaryngologic Imaging Interpretation", dt.date(2024, 6, 1))
    post = c.rate_entry("Ambulatory Otolaryngologic Imaging Interpretation", dt.date(2025, 6, 1))
    assert pre.rate_cents == 182625
    assert post.rate_cents == 208200
    # the amendment applies by service date, not invoice date -- there is
    # no separate concept of invoice date in the Contract model at all,
    # which is itself the check: the lookup only ever takes a service date.


def test_h3_service_added_by_amendment_not_billable_before_effective_date():
    c = build_h3()
    assert c.rate_entry("Advanced Dermatologic Nutritional Support", dt.date(2024, 6, 1)) is None
    assert c.rate_entry("Advanced Dermatologic Nutritional Support", dt.date(2025, 6, 1)) is not None


def test_h3_unaffected_service_is_flat_across_the_term():
    c = build_h3()
    a = c.rate_entry("Advanced Gastrointestinal Telemetry Monitoring", dt.date(2024, 6, 1))
    b = c.rate_entry("Advanced Gastrointestinal Telemetry Monitoring", dt.date(2025, 6, 1))
    assert a.rate_cents == b.rate_cents == 128100


def test_dual_unit_basis_service_carries_both_bases_on_one_rate_entry():
    c = build_h3()
    entries = c.all_rate_entries("Focused Urologic Telemetry Monitoring")
    assert len(entries) == 1  # not split into two competing entries
    assert set(entries[0].unit_basis) >= {"per_hour", "per_item", "per_hour_per_item"}


def test_exclusion_window_is_one_directional():
    # "Advanced Metabolic Anaesthesia Administration is not billable within
    # 7 days of Standard Endocrine Endoscopic Procedure" restricts only the
    # first service; the second remains billable regardless. Getting this
    # backwards produced a real false positive during development
    # (INV-H1-000830) -- see decision log.
    c = build_h1()
    restricted = [e.excluded_by for e in c.exclusions_for("Advanced Metabolic Anaesthesia Administration")]
    unrestricted = c.exclusions_for("Standard Endocrine Endoscopic Procedure")
    assert "Standard Endocrine Endoscopic Procedure" in restricted
    assert unrestricted == []


def test_h4_base_rate_table_has_no_cap_column_caps_come_from_section_6():
    c = build_h4()
    assert len(c.services) == 98
    entry = c.rate_entry("Advanced Orthopaedic Ward Bed Occupancy", dt.date(2024, 6, 1))
    assert entry.daily_cap is None  # the RateEntry itself never carries one for H4
    assert c.daily_cap("Advanced Orthopaedic Ward Bed Occupancy") == 12  # comes from the separate cap table


def test_h4_bundle_table_column_order_is_read_correctly():
    # Section 7's header is "Service A | Substituted rate A | Service B |
    # Substituted rate B" -- interleaved, unlike hospital 1/3's grouped
    # "Service A | Service B | Bundled rate A | Bundled rate B". Reading
    # this with the wrong column mapping would swap which service gets
    # which bundled rate without erroring.
    c = build_h4()
    b = c.bundle_partner("Ambulatory Obstetric Case Conference")
    assert b.service_a == "Ambulatory Obstetric Case Conference"
    assert b.rate_a_cents == 11875  # GBP 118.75, not Focused Vascular Infusion Therapy's 49.00
    assert b.service_b == "Focused Vascular Infusion Therapy"
    assert b.rate_b_cents == 4900


def test_h4_has_no_weekend_uplifts():
    # Section 10 is headed but reads "_None._" -- no table at all.
    c = build_h4()
    assert c.weekend_uplifts == []


def test_h5_facility_and_plan_tier_multipliers_are_real():
    c = build_h5()
    assert len(c.services) == 84
    # F-MAIN is always the baseline (1.0); F-NORTH/F-COAST genuinely differ.
    assert c.facility_multiplier("Advanced Cardiac Ventilation Support", "F-MAIN") == 1.0
    assert c.facility_multiplier("Advanced Cardiac Ventilation Support", "F-NORTH") == 1.1
    assert c.facility_multiplier("Advanced Cardiac Ventilation Support", "F-COAST") == 0.92
    assert c.plan_tier_multiplier("Advanced Cardiac Ventilation Support", "GOLD") == 0.92
    # every other hospital has no such table, so this must default to a
    # true no-op rather than raise or silently return 0.
    assert c.facility_multiplier("Advanced Cardiac Ventilation Support", "F-DOES-NOT-EXIST") == 1.0


def test_h5_is_the_only_contract_with_real_multipliers():
    for build in (build_h1, build_h2, build_h3, build_h4):
        c = build()
        assert c.facility_multipliers == []
        assert c.plan_tier_multipliers == []


def test_h2_prose_reader_finds_every_service_and_skips_all_boilerplate():
    # 12 "Contracted Services" groups of 6 plus one final group of 4; the
    # dozen or so filler Articles (Notices, Audit Rights, Force Majeure,
    # Confidentiality...) between them must contribute zero services.
    c = build_h2()
    assert len(c.services) == 76
    assert c.rate_entry("Advanced Infectious Isolation Room Occupancy", dt.date(2024, 6, 1)).rate_cents == 164825


def test_h2_prose_mechanics_parsed_from_free_text_not_tables():
    c = build_h2()
    assert c.daily_cap("Advanced Infectious Isolation Room Occupancy") == 24
    assert c.weekend_uplift("Emergency Renal Infusion Therapy").uplift == 0.12
    premium = c.threshold_premium("Focused Palliative Recovery Room Occupancy")
    assert (premium.daily_qty_threshold, premium.uplift) == (12, 0.30)
    tiers = c.volume_discount_tiers("Postoperative Ophthalmic Anaesthesia Administration")
    assert [(t.cumulative_threshold, t.discount) for t in tiers] == [(80, 0.10), (240, 0.30)]
    exclusions = c.exclusions_for("Intermittent Psychiatric Laboratory Panel")
    assert (exclusions[0].window_days, exclusions[0].excluded_by) == (7, "Emergency Pulmonary Ventilation Support")


def test_h2_bundle_pairs_declared_from_both_sides_agree():
    # Each of hospital 2's 3 bundle pairs is stated twice, once in each
    # partner's own clause -- 6 Bundle records for 3 relationships. Both
    # records must resolve to the same rate for the same service regardless
    # of which one happens to be indexed.
    c = build_h2()
    a = c.bundle_partner("Postoperative Orthopaedic Isolation Room Occupancy")
    rate = a.rate_a_cents if a.service_a == "Postoperative Orthopaedic Isolation Room Occupancy" else a.rate_b_cents
    assert rate == 90350  # GBP 903.50, regardless of which side's clause won the dict


def test_h2_invoice_submission_window_is_the_only_hospital_with_one():
    assert build_h2().invoice_submission_window_days == 60
    for build in (build_h1, build_h3, build_h4, build_h5):
        assert build().invoice_submission_window_days is None
