# Decision log

One page, per the brief: assumptions, ambiguities found, and what I did about
each. Invoice IDs are cited so every claim below is checkable against the
data.

## Scope decision

The submission covers hospitals 2, 3, 4 and 5; hospital 1 remains
development/calibration only, per the brief. The original pass (within the
stated 6-8 hour budget) covered hospital 1 plus hospital 3 only, on the
reasoning that hospital 3's mid-term amendment — "an amendment that repriced
services halfway through the term" — was the most structurally distinct
challenge among the remaining four, worth going deep on rather than spreading
thin. Hospitals 2, 4 and 5 were added in a second pass; see "Extending to
hospitals 2, 4 and 5" below for what each one turned out to need. The
original reasoning for picking 3 first still stands as the sequencing
decision within the original time budget — see the write-up for that
version's account of it.

## Ambiguities in the contract text, and how I resolved them

The three contracts I read (hospital 1, 3) state the rules precisely enough
that most of this was mechanical. Three points were genuinely ambiguous, and
I resolved all three empirically against the hospital 1 labels rather than by
guessing, because hospital 1 is exactly the tool the brief gives us for this:

1. **Does a threshold premium uplift the whole day's quantity, or just the
   units above the threshold?** Section 5.1 says the premium is "assessed
   against the aggregate quantity... not against the quantity on any one
   line item," which settles *how you decide the premium applies* but not
   *what it applies to*. INV-H1-000348 (`premium_omitted`) has 13 hours of
   "Preoperative Geriatric Ventilation Support" against an 8-hour threshold;
   reproducing the label's `expected_total_cents` only works if all 13 hours
   are uplifted, not just the 5 above the threshold. I used "whole day, all
   units" throughout.

2. **What is the "expected" quantity when a daily cap is exceeded?** The
   contract bounds the cap from above but says nothing about what the
   pre-corruption quantity was. Two clean, single-category examples
   (INV-H1-000015 and INV-H1-000725) each needed a *different* clamp amount
   to reproduce the label exactly, which tells me the original quantity is
   generated upstream of the visible data and is not recoverable from the
   contract at all — even a human auditor working from the invoice alone
   could not derive it. I use the standard, defensible convention instead
   (bill up to the cap, excess unpaid) and flag every `daily_cap_exceeded`
   row with reduced confidence specifically on the dollar figure, while
   staying confident that the invoice *is* wrong.

3. **Do exclusion windows run both ways?** Section 10.1's "measured in
   either direction" reads, on a first pass, as "either service blocks the
   other." INV-H1-000830 (a labelled-correct invoice) falsified that: it
   bills "Standard Endocrine Endoscopic Procedure" near an "Advanced
   Metabolic Anaesthesia Administration" and is *not* an error. The "either
   direction" clause governs how the day-window is measured around the
   *named* excluding service, not whether the rule runs symmetrically. Only
   the named service in the "Service" column goes unpaid.

## Assumptions I made without a labelled example to check them against

- **Cumulative volume-discount utilisation counts every billed unit**,
  including units later found to be over a daily cap or otherwise
  non-payable. The contracts say utilisation is counted in units "billed,"
  not "paid," so I count what was billed.
- **A reused invoice_id's line items are not reliably attributable to either
  physical invoice.** Two rows in hospital 1's own invoice file share
  `INV-H1-000068`; the line items reference only the id, so there is no way
  to tell which lines belong to which header row. I flag the collision
  (deterministic, high confidence) but report the invoice's own billed total
  as its expected total rather than invent an attribution, and cap
  confidence at 0.7 on that row to reflect that the dollar figure is a
  stated convention, not a verified one. This also turned out to matter
  mechanically: naively joining line items to invoices on `invoice_id`
  double-counts every line under a reused id in the cross-invoice running
  totals (cumulative discounts, threshold premiums, caps) for every
  service that happens to sequence afterward. I deduplicate the invoice
  header before that join.
- **A line's own billed price is discarded when computing "expected"; only
  quantity and the resolved service matter.** Recomputing every line from
  the contract's own rules, instead of trying to patch the billed price,
  is what let arithmetic-corruption cases (`line_total_arithmetic`) resolve
  exactly (INV-H1-000069) rather than approximately.
- **Facility and plan-tier multipliers are 1.0 throughout.** All three
  contracts I read state a single facility with no differential and
  identical rates across plan tiers, so steps (b) and (c) of the stated
  adjustment order are no-ops here; I still model them as an explicit stage
  so a future hospital that *does* differentiate slots in without a rewrite.
- **An unresolved or unrecognised service description is left at its billed
  amount.** INV-H1-000348's `unknown_service` line only reconciles to the
  label if left unchanged, which matches the brief's own philosophy: we
  cannot respectably invent a number for a service we cannot identify, so we
  say so (low confidence) rather than guess.

## Extending to hospitals 2, 4 and 5

The pricing engine (bundle → facility → plan-tier → premium → discount, in
that order) was written hospital-agnostically from the start, so extending
coverage was mostly a matter of writing three more contract *readers* against
the same `Contract` model — plus two places where the model itself needed a
genuinely new capability, not just a new reader:

- **Hospital 5 has real facility and plan-tier multipliers.** Every other
  hospital states these are 1.0; hospital 5's Table 2/3 genuinely vary them
  by facility code and plan tier. `Contract` gained
  `facility_multiplier()`/`plan_tier_multiplier()` (defaulting to 1.0 when a
  hospital has no such table, so hospitals 1-4 are unaffected), and the
  pricing loop applies them right after bundle substitution, before any
  premium — same position every contract's own clause 3.1-equivalent
  specifies. This also broke the price-anchoring matcher in a way worth
  recording: a service's *legitimate* price now varies by which of 9
  facility/tier combinations billed it, so the "one dominant price per
  description" assumption the matcher relied on for hospitals 1/3/4 no
  longer holds. Fixed by making the per-line price fallback facility/tier-
  aware (`matching.plausible_prices` now takes the billing line's own
  facility code and plan tier and adjusts the candidate rate before
  checking premium/discount variants on top of it). Before this fix,
  hospital 5 flagged 26% of invoices; after, 7.2%, in line with the other
  three.

- **Hospital 2 has no tables at all** ("Executed as a deed. No tables are
  used in this instrument.") — its 76 services are stated as free-standing
  prose paragraphs, interleaved with roughly a dozen entire Articles
  (Notices, Audit Rights, Force Majeure, Confidentiality, Data Protection,
  Change Control, Insurance, Termination...) that read as real contract
  boilerplate but, on inspection, carry zero pricing content — each is five
  near-identical templated sub-clauses restating the same non-obligation in
  slightly different words. This is pure volume, not a trap requiring
  special handling: the reader only ever looks for paragraphs matching "N.N
  In respect of...", so the filler Articles are never touched rather than
  explicitly excluded. What *is* a genuine trap, and one I deliberately did
  not chase: Article II defines "Service Day" as the 24-hour period from
  07:00 to 06:59 the next calendar day, not the calendar day itself — but
  the invoice data records only a Service *Date* (2.3), never a time, so
  there is no way to tell from the available fields whether a billed
  service should shift to the previous Service Day. I price every Service
  Date as its own Service Day, the only reading the data supports, and
  record this explicitly rather than silently ignore the clause.
  Separately, hospital 2 also states a real, checkable rule the others
  don't have — invoices must be submitted within 60 days of the discharge
  date (Article XIII) — which I did implement (`invoice_submitted_late`);
  it never actually fires against this dataset (the largest observed gap is
  10 days), which I take as the rule being genuinely unviolated here rather
  than evidence the check is broken, since I confirmed the underlying date
  arithmetic against the raw data directly.

- **Hospital 4's tables use a different column order for bundles**
  ("Service A | Substituted rate A | Service B | Substituted rate B",
  interleaved, vs. hospital 1/3's grouped "Service A | Service B | Bundled
  rate A | Bundled rate B") and its base-rate table carries no cap column at
  all (caps live only in a separate section) — both read correctly by
  writing the reader against the table's actual header row rather than
  assuming the shape from the other hospitals, which is the same discipline
  the generic table parser was built around in the first place.

One vocabulary lesson generalized across all three: each hospital's
free-text descriptions use their own abbreviation quirks (hospital 4 uses
`supv`/`vst` for "supervised"/"visit", neither of which appeared in hospital
1's vocabulary), and the fix each time was the same as during the original
hospital-3 work — sample the actual unresolved descriptions, add the missing
abbreviation once confirmed against the contract text, re-run. This is a
standing risk for any hospital's vocabulary I haven't specifically checked:
the matcher's abbreviation dictionary is only as complete as what's been
observed so far.

## A matching decision worth flagging explicitly

Line-item descriptions are genuinely ambiguous in a small number of cases —
some omit the specialty word entirely (e.g. "Procedure Immun Endosc" could be
either "Ambulatory" or "Preoperative Immunologic Endoscopic Procedure").
Where the aggregate description-level signal is ambiguous, I fall back to
each *individual* line's own price, checked against every rate the candidate
service could legitimately show (base rate, plus its one premium/discount/
weekend adjustment) — but only when the wording is at least weakly
consistent with that candidate. Price coincidence alone is not accepted as a
match: "Adv Renal Consultation" (not a contracted service at all) happens to
bill at exactly the base rate of the unrelated "Ambulatory Psychiatric
Dialysis Session," and an early version of the matcher matched it on that
basis alone. Requiring a minimum text-plausibility score fixed it; see the
evaluation report's failure-mode analysis for detail.
