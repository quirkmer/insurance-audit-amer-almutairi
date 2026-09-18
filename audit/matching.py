"""Resolve a hospital's free-text line-item description to a contracted
Service name.

The brief is explicit that this is real work, not a lookup: "the same
contracted service is described many different ways across the data" and
"establishing which contracted service a description refers to is part of
the task." Two independent signals are available per line item and we use
both:

1. **Economics.** Most line items are billed at an unadjusted contract
   rate. If a (unit_basis, unit_price_cents) pair matches exactly one
   contracted service (checking every rate window, since hospital 3's
   rates move on 1 Jan 2025), that is very strong evidence of which
   service was intended -- independent of the wording used to describe it.
2. **Text.** Descriptions are abbreviated, reordered, and sometimes carry
   a trailing internal billing code ("/NG-3022") that we strip as noise.
   Abbreviation is mostly truncation ("Interm" / "Intermittent", "Compr" /
   "Comprehensive") which a prefix match catches for free, plus a small
   set of genuine clinical shorthand ("Ent" = Otolaryngologic (ENT), "Msk"
   = Musculoskeletal, "GI" = Gastrointestinal, "Cr" = "Critical" or "Care"
   depending on context) that we encode explicitly, having read the
   billing vocabulary by hand.

We resolve each *distinct* description string once (there are only a few
hundred per hospital against ten-plus thousand line items), anchoring on
price+unit-basis wherever that is unambiguous and falling back to text
scoring otherwise. Text scoring is then cross-checked against the
price-anchored set as a held-out accuracy estimate for the matcher itself
(see `scripts/calibrate_h1.py`), which is what the confidence bands in the
final submission are based on.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from audit.model import Contract
from audit.money import half_up_cents

_CODE_SUFFIX_RE = re.compile(r"/[A-Za-z]{1,4}-?\d+\s*$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Genuine clinical/billing shorthand that is not a simple prefix of the
# canonical word. Built by reading the distinct description vocabulary
# against the rate schedules for hospitals 1, 3 and 4 (see prompts/003).
# Keys are lowercase tokens as they appear in descriptions.
IRREGULAR_ABBREVIATIONS: dict[str, str] = {
    "ent": "otolaryngologic",
    "msk": "musculoskeletal",
    "gi": "gastrointestinal",
    "ob": "obstetric",
    "obs": "observation",
    "spclst": "specialist",
    "rtn": "routine",
    "asst": "assisted",
    "amb": "ambulatory",
    "hm": "home",
    "wd": "wound",
    "cr": "care",  # "Critical Cr Occ" (care) vs "Critical Care" -- both resolve to "care"
    "bd": "bed",
    "rm": "room",
    "cs": "case",
    "svc": "service",
    "sess": "session",
    "img": "imaging",
    "interp": "interpretation",
    "diag": "diagnostic",
    "fract": "fraction",
    "radiother": "radiotherapy",
    "vent": "ventilation",
    "supp": "support",
    "nutr": "nutritional",
    "nurs": "nursing",
    "consult": "consultation",
    "transf": "transfusion",
    "transp": "transport",
    "physio": "physiotherapy",
    "prog": "programme",
    "occ": "occupancy",
    "anaes": "anaesthesia",
    "anesth": "anaesthesia",
    "admin": "administration",
    "pharma": "pharmaceutical",
    "pharm": "pharmaceutical",
    "disp": "dispensing",
    "lab": "laboratory",
    "spec": "specimen",
    "anal": "analysis",
    "monit": "monitoring",
    "iso": "isolation",
    "endosc": "endoscopic",
    "proc": "procedure",
    "dial": "dialysis",
    "biop": "biopsy",
    "foc": "focused",
    "cont": "continuous",
    "interm": "intermittent",
    "compr": "comprehensive",
    "postop": "postoperative",
    "preop": "preoperative",
    "adv": "advanced",
    "std": "standard",
    "ext": "extended",
    "beds": "bedside",
    "rehab": "rehabilitation",
    "derm": "dermatologic",
    "neuro": "neurological",
    "rheum": "rheumatologic",
    "pulm": "pulmonary",
    "immun": "immunologic",
    "metab": "metabolic",
    "ortho": "orthopaedic",
    "uro": "urologic",
    "urologic": "urologic",
    "obst": "obstetric",
    "paed": "paediatric",
    "hep": "hepatic",
    "infect": "infectious",
    "vasc": "vascular",
    "psych": "psychiatric",
    "ophth": "ophthalmic",
    "ger": "geriatric",
    "haem": "haematology",
    "onc": "oncology",
    "endo": "endocrine",
    "steril": "sterilisation",
    "recov": "recovery",
    "cardiac": "cardiac",
    "thtr": "theatre",
    "tm": "time",
    "spcm": "specimen",
    "anly": "analysis",
    "immun": "immunologic",
    "inpt": "inpatient",
    "supv": "supervised",
    "vst": "visit",
    "disch": "discharge",
    "plng": "planning",
    "obst": "obstetric",
    "paed": "paediatric",
}


def strip_billing_code(description: str) -> str:
    return _CODE_SUFFIX_RE.sub("", description).strip()


def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = text.replace("&", " and ")
    tokens = [t for t in _NON_ALNUM_RE.split(text) if t]
    return tokens


def expand_token(token: str) -> str:
    return IRREGULAR_ABBREVIATIONS.get(token, token)


def _token_match_score(desc_token: str, canon_token: str) -> float:
    expanded = expand_token(desc_token)
    if expanded == canon_token:
        return 1.0 if desc_token == canon_token else 0.95  # exact vs. irregular-abbrev hit
    if len(desc_token) >= 3 and canon_token.startswith(desc_token):
        return 0.85  # plain truncation, e.g. "interm" / "intermittent"
    if len(desc_token) >= 3 and canon_token.startswith(expanded):
        return 0.8
    return 0.0


@dataclass(frozen=True)
class ServiceCatalogEntry:
    service: str
    tokens: tuple[str, ...]


def build_catalog(services: list[str]) -> list[ServiceCatalogEntry]:
    return [ServiceCatalogEntry(s, tuple(tokenize(s))) for s in services]


def build_unit_basis_index(contract) -> dict[str, set[str]]:
    """service -> every unit_basis code it has ever been priced under."""
    index: dict[str, set[str]] = {}
    for r in contract.rates:
        index.setdefault(r.service, set()).update(r.unit_basis)
    return index


def score_description(desc_tokens: list[str], canon_tokens: tuple[str, ...]) -> float:
    """Greedy best-effort coverage score in [0, 1]: for every canonical
    token, take the best-scoring unused description token. Unmatched
    canonical tokens count as zero. Order-independent by design, since
    descriptions frequently reorder the noun phrase.
    """
    remaining = list(desc_tokens)
    total = 0.0
    for canon_token in canon_tokens:
        best_i, best_score = -1, 0.0
        for i, dt in enumerate(remaining):
            s = _token_match_score(dt, canon_token)
            if s > best_score:
                best_score, best_i = s, i
        if best_i >= 0:
            total += best_score
            remaining.pop(best_i)
    return total / len(canon_tokens)


@dataclass(frozen=True)
class MatchResult:
    service: str | None
    confidence: float
    method: str  # "price_anchor" | "text" | "unresolved"


def plausible_prices(
    contract: Contract, service: str, base_rate_cents: int,
    facility_code: str | None = None, plan_tier: str | None = None,
) -> set[int]:
    """Every per-unit price `service` could legitimately show up at: the
    base rate (adjusted for facility and plan tier where given -- a no-op
    1.0 multiplier for every hospital except hospital 5), or that rate
    under its one non-bundle adjustment (a threshold premium, a weekend
    uplift, or any cumulative-discount tier -- never more than one of
    these fires on the same line, per the contracts' own ordering rules).
    Bundle-substituted rates are deliberately excluded here: they depend
    on same-day co-delivery of a specific partner service, which this
    description-level, price-only check has no way to confirm, so
    including them would let a coincidence masquerade as a match.
    """
    base_rate_cents = half_up_cents(base_rate_cents, contract.facility_multiplier(service, facility_code))
    base_rate_cents = half_up_cents(base_rate_cents, contract.plan_tier_multiplier(service, plan_tier))
    prices = {base_rate_cents}
    premium = contract.threshold_premium(service)
    if premium:
        prices.add(half_up_cents(base_rate_cents, 1 + premium.uplift))
    weekend = contract.weekend_uplift(service)
    if weekend:
        prices.add(half_up_cents(base_rate_cents, 1 + weekend.uplift))
    for tier in contract.volume_discount_tiers(service):
        prices.add(half_up_cents(base_rate_cents, 1 - tier.discount))
    return prices


def resolve_by_price(
    unit_basis: str, unit_price_cents: int, contract: Contract, service_dates: list,
    facility_code: str | None = None, plan_tier: str | None = None,
) -> list[str]:
    """Every service whose rate -- or one of its legitimate adjusted rates,
    see `plausible_prices` -- equals this (unit_basis, price) on at least
    one of the service dates this description was actually billed with.
    `facility_code`/`plan_tier` narrow this to one specific invoice's
    combination; omit them (as the aggregate, cross-invoice description
    resolver does) to check only the unadjusted base rate.
    """
    hits = set()
    for r in contract.rates:
        prices = plausible_prices(contract, r.service, r.rate_cents, facility_code, plan_tier)
        if unit_basis not in r.unit_basis or unit_price_cents not in prices:
            continue
        for d in service_dates:
            if r.effective_from <= d <= r.effective_to:
                hits.add(r.service)
                break
    return sorted(hits)


def resolve_descriptions(
    contract: Contract,
    description_stats: dict[str, dict],
    text_score_threshold: float = 0.65,
) -> dict[str, MatchResult]:
    """description_stats: description -> {
        'unit_basis': Counter, 'unit_price_cents': Counter, 'service_dates': [date,...]
    }
    Returns description -> MatchResult.
    """
    catalog = build_catalog(contract.services)
    catalog_by_name = {c.service: c for c in catalog}
    unit_basis_index = build_unit_basis_index(contract)
    results: dict[str, MatchResult] = {}

    for desc, stats in description_stats.items():
        cleaned = strip_billing_code(desc)
        desc_tokens = tokenize(cleaned)

        # 1) price anchor: does the *dominant* (unit_basis, price) pairing
        # for this description string match exactly one contracted service?
        basis_counter: Counter = stats["unit_basis"]
        price_counter: Counter = stats["unit_price_cents"]
        dominant_basis, basis_n = basis_counter.most_common(1)[0]
        dominant_price, price_n = price_counter.most_common(1)[0]
        basis_share = basis_n / sum(basis_counter.values())
        price_share = price_n / sum(price_counter.values())

        anchor_hits: list[str] = []
        if basis_share >= 0.8 and price_share >= 0.8:
            anchor_hits = resolve_by_price(dominant_basis, dominant_price, contract, stats["service_dates"])

        if len(anchor_hits) == 1:
            candidate = anchor_hits[0]
            # A price match on its own is not enough: a fabricated service
            # description can coincide with a real rate purely by chance
            # (e.g. "Adv Renal Consultation", a service that does not exist
            # in this contract, happened to bill at exactly the base rate of
            # the unrelated "Ambulatory Psychiatric Dialysis Session"). Only
            # trust the price anchor if the wording is at least plausibly
            # related to the candidate; otherwise fall through to text
            # scoring, which will correctly find nothing good enough and
            # report unresolved / unknown_service.
            plausibility = score_description(desc_tokens, catalog_by_name[candidate].tokens)
            if plausibility >= 0.3:
                results[desc] = MatchResult(candidate, 0.97, "price_anchor")
                continue

        # 2) text score against candidate services. If the price anchor was
        # ambiguous, restrict to that ambiguous set so price still helps
        # narrow it down. Otherwise, prefer services whose contracted unit
        # basis matches what was actually billed -- e.g. "derm physiotherapy
        # sess" billed per_visit should out-rank a per_hour service of
        # almost the same name purely on textual similarity; the billed
        # basis is real evidence text scoring alone would ignore.
        if anchor_hits:
            candidates = [c for c in catalog if c.service in anchor_hits]
        else:
            basis_compatible = [c for c in catalog if dominant_basis in unit_basis_index.get(c.service, set())]
            candidates = basis_compatible or catalog
        scored = sorted(
            ((score_description(desc_tokens, c.tokens), c.service) for c in candidates),
            reverse=True,
        )
        if not scored:
            results[desc] = MatchResult(None, 0.0, "unresolved")
            continue

        top_score, top_service = scored[0]
        runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
        if top_score >= text_score_threshold and (top_score - runner_up_score) >= 0.05:
            # confidence blends absolute match quality with how much better
            # it is than the next best candidate (separation).
            confidence = min(0.95, 0.5 * top_score + 0.5 * min(1.0, (top_score - runner_up_score) * 4))
            results[desc] = MatchResult(top_service, round(confidence, 3), "text")
        else:
            results[desc] = MatchResult(None, round(top_score, 3), "unresolved")

    return results
