"""Build a `Contract` for hospital 2 from master_services_agreement.md.

Hospital 2 is the "forty pages of prose" contract: no tables at all
("Executed as a deed. No tables are used in this instrument.") -- every
one of its 76 contracted services is stated as a single free-standing
paragraph ("X.Y In respect of <Service>, the Provider shall invoice the
Payer at the rate of GBP <rate> per <unit>. ... [optional premium/cap/
discount/bundle/exclusion clause] ... [boilerplate]."), and those Service
paragraphs are interleaved with entire Articles (Notices, Records, Audit
Rights, Recovery of Overpayments, Confidentiality, Data Protection,
Change Control, Force Majeure, Insurance, Termination, Governing Law,
Entire Agreement, Severance...) that carry zero pricing content and exist
purely as volume: each is five near-identical templated sub-clauses,
verified by reading every one of them -- none contains a rate, a
threshold, or a service name. This reader skips them by construction: it
only ever looks at paragraphs matching "N.N In respect of ...", so the
filler is never touched.

Because the source is prose rather than a table, the "generic table
parser, per-hospital semantic reader" split used for hospitals 1/3/4/5
doesn't apply the same way -- there is no table to parse generically.
What's generic here instead is the pricing engine downstream: every
mechanic below (weekend uplift, threshold premium, daily cap, one-or-two
-tier cumulative discount, bundle, exclusion window) is exactly the same
mechanic already modelled in `audit/model.py` for the tabular hospitals,
just prose-encoded rather than table-encoded. The extraction below reads
each mechanic's own fixed sentence template with a regex per mechanic,
independently of the others' position in the paragraph (the order the
optional clauses appear in varies paragraph to paragraph), and hands the
matched numbers to the same `parse_int`/`parse_percent` helpers used
everywhere else -- both already prefer a parenthesised digit ("twelve
(12)") over the spelled-out word next to it, which is the only numeral
form this contract uses.

One clause we deliberately do *not* attempt: Article II defines "Service
Day" as the 24-hour period from 07:00 to 06:59 the next calendar day --
not the calendar day itself, unlike every other hospital's plain
same-day definition. This reads as the kind of quiet trap the exercise
warns about, but it has no actual effect on anything we can compute: the
invoice data records only a Service *Date* (2.3), never a time of day,
so there is no way to tell whether a given billed service fell before or
after 07:00 on its recorded date. We price every Service Date as if it
is its own Service Day, which is the only reading the available fields
support -- see the decision log.

Article XIII (60-day invoice-submission window from the discharge date)
*is* implemented, via `Contract.invoice_submission_window_days`, since
unlike the Service Day question it is directly checkable from the
invoice header fields.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

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

CONTRACT_PATH = Path(__file__).parent.parent / "contracts" / "hospital_2" / "master_services_agreement.md"

TERM_START = dt.date(2024, 1, 1)
TERM_END = dt.date(2025, 12, 31)

_CLAUSE_RE = re.compile(r"^\d+\.\d+ In respect of ", re.MULTILINE)
_BASE_RE = re.compile(
    r"In respect of (.+?), the Provider shall invoice the Payer at the rate of GBP ([\d,]+\.\d\d) per (.+?)\."
)
_WEEKEND_RE = re.compile(
    r"does not fall on a Business Day, the rate applicable to it shall be increased by ([^.]+)\."
)
_THRESHOLD_RE = re.compile(
    r"Where the aggregate quantity of this Service delivered to a Patient on a single Service Day exceeds ([^.]+)\."
)
_CAP_RE = re.compile(r"shall not bill more than ([^.]+) of this Service for a Patient on a single Service Day")
_DISCOUNT_RE = re.compile(r"Where cumulative utilisation of this Service exceeds ([^.]+)\.")
_BUNDLE_RE = re.compile(
    r"Where this Service and (.+?) are both delivered to the same Patient on the same Service Day, "
    r"the two shall be billed as a bundle, this Service at GBP ([\d,]+\.\d\d) per .+? "
    r"and (.+?) at GBP ([\d,]+\.\d\d) per .+?, in substitution for their standalone rates",
    re.DOTALL,
)
_EXCLUSION_RE = re.compile(
    r"This Service is not billable where (.+?) has been delivered to the same Patient within (.+?) days of the Service Date"
)


def _split_service_paragraphs(text: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if _CLAUSE_RE.match(p.strip())]


def build_contract() -> Contract:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    paragraphs = _split_service_paragraphs(text)

    rates: list[RateEntry] = []
    premiums: list[ThresholdPremium] = []
    weekend: list[WeekendUplift] = []
    caps: list[DailyCap] = []
    discounts: list[VolumeDiscount] = []
    bundles: list[Bundle] = []
    exclusions: list[ExclusionWindow] = []

    for para in paragraphs:
        base = _BASE_RE.search(para)
        if not base:
            raise ValueError(f"could not find a base rate in clause:\n{para[:200]}")
        service = base.group(1).strip()
        rate_cents = parse_gbp_to_cents("GBP " + base.group(2))
        basis = tuple(normalize_unit_basis("per " + base.group(3).strip()))
        rates.append(RateEntry(service, basis, rate_cents, None, TERM_START, TERM_END))

        if m := _WEEKEND_RE.search(para):
            weekend.append(WeekendUplift(service, parse_percent(m.group(1))))

        if m := _THRESHOLD_RE.search(para):
            premiums.append(ThresholdPremium(service, parse_int(m.group(1)), parse_percent(m.group(1))))

        if m := _CAP_RE.search(para):
            caps.append(DailyCap(service, parse_int(m.group(1))))

        for m in _DISCOUNT_RE.finditer(para):
            discounts.append(VolumeDiscount(service, parse_int(m.group(1)), parse_percent(m.group(1))))

        if m := _BUNDLE_RE.search(para):
            other, rate_a, other2, rate_b = m.groups()
            bundles.append(
                Bundle(service, other2.strip(), parse_gbp_to_cents("GBP " + rate_a), parse_gbp_to_cents("GBP " + rate_b))
            )
            assert other.strip() == other2.strip(), f"bundle partner named twice, disagreeing: {other!r} vs {other2!r}"

        if m := _EXCLUSION_RE.search(para):
            other, days = m.groups()
            exclusions.append(ExclusionWindow(service, parse_int(days), other.strip()))

    # de-duplicate caps declared via the rate-table daily-cap column vs a
    # separately-worded cap clause -- doesn't happen in this contract (caps
    # here only ever come from the "shall not bill more than" clause), but
    # keep one source of truth per service the same way the other readers do.
    cap_map: dict[str, int] = {}
    for c in caps:
        cap_map[c.service] = c.max_qty
    daily_caps = [DailyCap(s, q) for s, q in cap_map.items()]

    return Contract(
        hospital_id="H2",
        contract_number="INS-H2-2024-1183",
        term_start=TERM_START,
        term_end=TERM_END,
        rates=rates,
        threshold_premiums=premiums,
        weekend_uplifts=weekend,
        volume_discounts=discounts,
        daily_caps=daily_caps,
        bundles=bundles,
        exclusion_windows=exclusions,
        invoice_submission_window_days=60,
    )
