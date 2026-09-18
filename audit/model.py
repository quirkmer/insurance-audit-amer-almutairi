"""Contract data model shared by hospitals 1, 3 and 4.

A `Contract` is a plain-data snapshot of "what does this payer owe for this
service on this date", built once per hospital from the parsed contract
tables. The pricing engine (`audit/pricing.py`) is written against this
model and knows nothing about Markdown, so hospital 3's mid-term amendment
is just "a service can have more than one RateEntry with different
effective windows" rather than a special case in the pricing logic.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RateEntry:
    service: str
    unit_basis: tuple[str, ...]  # one or more accepted billed unit_basis codes
    rate_cents: int
    daily_cap: int | None
    effective_from: dt.date
    effective_to: dt.date


@dataclass(frozen=True)
class ThresholdPremium:
    service: str
    daily_qty_threshold: int  # premium applies when aggregate daily qty > this
    uplift: float


@dataclass(frozen=True)
class WeekendUplift:
    service: str
    uplift: float


@dataclass(frozen=True)
class VolumeDiscount:
    service: str
    cumulative_threshold: int  # discount applies when prior cumulative qty > this
    discount: float


@dataclass(frozen=True)
class DailyCap:
    service: str
    max_qty: int


@dataclass(frozen=True)
class Bundle:
    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int


@dataclass(frozen=True)
class ExclusionWindow:
    service: str
    window_days: int
    excluded_by: str


@dataclass(frozen=True)
class FacilityMultiplier:
    service: str
    facility_code: str
    multiplier: float


@dataclass(frozen=True)
class PlanTierMultiplier:
    service: str
    plan_tier: str
    multiplier: float


@dataclass
class Contract:
    hospital_id: str
    contract_number: str
    term_start: dt.date
    term_end: dt.date
    rates: list[RateEntry] = field(default_factory=list)
    threshold_premiums: list[ThresholdPremium] = field(default_factory=list)
    weekend_uplifts: list[WeekendUplift] = field(default_factory=list)
    volume_discounts: list[VolumeDiscount] = field(default_factory=list)
    daily_caps: list[DailyCap] = field(default_factory=list)
    bundles: list[Bundle] = field(default_factory=list)
    exclusion_windows: list[ExclusionWindow] = field(default_factory=list)
    # Only hospital 5 has real facility/plan-tier differentials; every other
    # hospital states (and we assume, per its own contract text) that these
    # are 1.0 everywhere, so an empty list here is a genuine no-op, not a
    # simplification -- facility_multiplier()/plan_tier_multiplier() return
    # 1.0 when a (service, code) pair isn't listed.
    facility_multipliers: list[FacilityMultiplier] = field(default_factory=list)
    plan_tier_multipliers: list[PlanTierMultiplier] = field(default_factory=list)
    invoice_submission_window_days: int | None = None  # hospital 2 only

    def __post_init__(self):
        self._services = sorted({r.service for r in self.rates})
        self._rates_by_service: dict[str, list[RateEntry]] = {}
        for r in self.rates:
            self._rates_by_service.setdefault(r.service, []).append(r)
        self._premium_by_service = {p.service: p for p in self.threshold_premiums}
        self._weekend_by_service = {w.service: w for w in self.weekend_uplifts}
        self._cap_by_service = {c.service: c.max_qty for c in self.daily_caps}
        self._discounts_by_service: dict[str, list[VolumeDiscount]] = {}
        for d in self.volume_discounts:
            self._discounts_by_service.setdefault(d.service, []).append(d)
        for svc in self._discounts_by_service:
            self._discounts_by_service[svc].sort(key=lambda d: d.cumulative_threshold)
        self._bundle_partner: dict[str, Bundle] = {}
        for b in self.bundles:
            self._bundle_partner[b.service_a] = b
            self._bundle_partner[b.service_b] = b
        # NOTE: exclusion windows are one-directional in *which service goes
        # unpaid* -- "Service | Not billable within N days | Of this Service"
        # only restricts the named Service, not its partner. The contracts'
        # "measured in either direction" clause governs how the N-day window
        # is measured around the partner's date (both earlier and later),
        # not whether the rule also runs the other way. Confirmed against
        # INV-H1-000830 in hospital 1: billing "Standard Endocrine Endoscopic
        # Procedure" near an "Advanced Metabolic Anaesthesia Administration"
        # is fine; only the reverse pairing is restricted.
        self._exclusions_by_service: dict[str, list[ExclusionWindow]] = {}
        for e in self.exclusion_windows:
            self._exclusions_by_service.setdefault(e.service, []).append(e)
        self._facility_mult: dict[tuple[str, str], float] = {
            (f.service, f.facility_code): f.multiplier for f in self.facility_multipliers
        }
        self._tier_mult: dict[tuple[str, str], float] = {
            (t.service, t.plan_tier): t.multiplier for t in self.plan_tier_multipliers
        }

    @property
    def services(self) -> list[str]:
        return self._services

    def rate_entry(self, service: str, service_date: dt.date) -> RateEntry | None:
        for r in self._rates_by_service.get(service, []):
            if r.effective_from <= service_date <= r.effective_to:
                return r
        return None

    def all_rate_entries(self, service: str) -> list[RateEntry]:
        return self._rates_by_service.get(service, [])

    def threshold_premium(self, service: str) -> ThresholdPremium | None:
        return self._premium_by_service.get(service)

    def weekend_uplift(self, service: str) -> WeekendUplift | None:
        return self._weekend_by_service.get(service)

    def daily_cap(self, service: str) -> int | None:
        return self._cap_by_service.get(service)

    def volume_discount_tiers(self, service: str) -> list[VolumeDiscount]:
        return self._discounts_by_service.get(service, [])

    def bundle_partner(self, service: str) -> Bundle | None:
        return self._bundle_partner.get(service)

    def exclusions_for(self, service: str) -> list[ExclusionWindow]:
        return self._exclusions_by_service.get(service, [])

    def facility_multiplier(self, service: str, facility_code: str | None) -> float:
        return self._facility_mult.get((service, facility_code), 1.0)

    def plan_tier_multiplier(self, service: str, plan_tier: str | None) -> float:
        return self._tier_mult.get((service, plan_tier), 1.0)
