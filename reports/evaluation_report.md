# Evaluation report

Measured on the hospital 1 development set (913 invoices, 58 labelled
erroneous), which the brief says is unscored and for calibration only.
Reproduce with `python scripts/calibrate_h1.py`.

## Headline numbers

| metric | value |
|---|---|
| precision (flagged invoices that are truly erroneous) | 1.000 |
| recall (erroneous invoices we flag) | 1.000 |
| F1 | 1.000 |
| correct invoices where our recomputed total exactly matches billed | 100.0% (855/855) |

These numbers are the *final* state, after the fixes described below. I am
reporting them plainly, but treat a perfect score on 58 positive examples
with appropriate caution: at this sample size a single-digit number of
additional bugs would not necessarily show up as a miss. The failure-mode
analysis below is what I'd point to instead of the headline number as
evidence of how the approach behaves, because those bugs are the ones
*this specific dataset* happened to be able to surface, and hospitals 2-5
have no labels to surface whatever they hide.

## Cross-hospital sanity check (no ground truth, so this is a weaker claim)

Hospitals 2, 4 and 5 have no labels, so the check available for them is
coarser: does the overall flag rate land in the same ballpark as hospital
1's known ~6.4%, once the same kind of debugging pass that hospital 1's
labels drove is applied by hand instead? All four converged to a tight,
plausible band:

| hospital | invoices | flagged | flagged rate | mean confidence (flagged / unflagged) |
|---|---|---|---|---|
| 2 | 1,125 | 76 | 6.8% | 0.75 / 0.90 |
| 3 | 932 | 70 | 7.5% | — |
| 4 | 835 | 63 | 7.5% | 0.76 / 0.90 |
| 5 | 1,050 | 76 | 7.2% | 0.76 / 0.89 |

Before the fixes described in failure modes 5 and 6 below, hospitals 4 and 5
initially came back at 13.7% and 26.1% respectively — the gap between that
and the ~7% they settled at is entirely the two bugs described below, not a
genuine difference in error rate between hospitals. I'm reporting the
before/after because a submission that quietly started at 26% and I "fixed
it until the number looked right" is a weaker claim than one where I can
point to the specific, checkable bug that explains the gap; both numbers are
in the repository's history for that reason.

## Per-category performance (hospital 1)

| category | n | recall | expected_total exact match rate |
|---|---|---|---|
| unknown_service | 12 | 1.00 | 1.00 |
| wrong_unit_basis | 11 | 1.00 | 0.91 |
| unit_price_mismatch | 10 | 1.00 | 1.00 |
| malformed_service_date | 6 | 1.00 | 1.00 |
| line_total_arithmetic | 6 | 1.00 | 1.00 |
| premium_incorrectly_applied | 6 | 1.00 | 0.83 |
| invoice_total_mismatch | 6 | 1.00 | 0.83 |
| bundle_not_applied | 5 | 1.00 | 1.00 |
| contract_number_mismatch | 5 | 1.00 | 0.80 |
| duplicate_invoice_id | 5 | 1.00 | 0.00 |
| service_date_out_of_window | 5 | 1.00 | 1.00 |
| service_date_after_invoice_date | 5 | 1.00 | 1.00 |
| exclusion_window_violation | 4 | 1.00 | 0.75 |
| cross_invoice_duplicate | 4 | 1.00 | 1.00 |
| daily_cap_exceeded | 4 | 1.00 | 0.00 |
| volume_discount_incorrectly_applied | 4 | 1.00 | 1.00 |
| volume_discount_omitted | 4 | 1.00 | 1.00 |
| premium_omitted | 3 | 1.00 | 1.00 |

*Recall* here means "the invoice was flagged at all" — a multi-category
invoice counts toward every one of its labelled categories. *Exact match
rate* is stricter: our `expected_total_cents` equals the label's, not just
"flagged=1". Two categories are structurally at 0% and are not bugs:

- **`daily_cap_exceeded`** — the pre-corruption quantity is not
  recoverable from the contract (see decision log). We report the
  standard clamp-to-cap convention and flag these rows with reduced
  confidence (0.65 ceiling) specifically because of this.
- **`duplicate_invoice_id`** — a reused id's line items cannot be reliably
  split between the two invoices that share it. We report the invoice's
  own billed total and flag the id collision itself at reduced confidence
  (0.7) rather than the usual 0.95 for a purely structural fact.

Every other category resolves exactly once its category-defining mechanism
is modelled correctly, because the underlying corruption is always a
substitution of one *derivable* number (a rate, a total, a date-driven
adjustment) for another — nothing about them is inherently unrecoverable.

## Confidence calibration

| confidence bucket | n (flagged) | empirical precision |
|---|---|---|
| 0.30 - 0.50 | 13 | 1.00 |
| 0.50 - 0.70 | 9 | 1.00 |
| 0.70 - 0.85 | 29 | 1.00 |
| 0.85 - 1.00 | 7 | 1.00 |

Every bucket shows 1.00 precision because hospital 1's flagged set has zero
false positives after the fixes below — there's nothing left to
miscalibrate against. That is a ceiling effect from a small, now-clean dev
set, not evidence that the confidence *number* is well-calibrated in
general; the more meaningful calibration claim is qualitative: confidence
is systematically higher for invoices whose flag rests only on arithmetic
or ID facts (0.95, no service resolution involved) and lower wherever a
service match, a cap clamp, or an unresolved description is load-bearing
(0.2-0.7). The mean confidence on flagged hospital-3 rows (0.62) sits well
below the mean on unflagged rows (0.90) for the same reason, which is the
behaviour I was aiming for given the brief's own instruction that a stated
uncertainty is worth more than a confident guess.

## Failure-mode analysis

Six systematic ways this approach went wrong during development. The first
four were caught and fixed via the hospital-1 labels, before hospitals 2, 4
and 5 were in scope at all; the last two only surfaced once those three
hospitals' unlabelled data was run through and the flag rate came back
implausibly high. I'm listing these as the ways to bet on where a
*remaining*, uncaught bug is most likely to live, since the mechanism that
produced each one is generic rather than a one-off typo.

**1. Rounding mode drift compounds silently across every discount/premium
line.** The contracts specify "half up" rounding; an early version used
Python's bare `round()`, which is banker's rounding (round-half-to-even) on
floats. The two disagree only on exact-half-cent cases, so most lines were
fine — but 6% of otherwise-correct hospital-1 invoices came out one or two
cents wrong (e.g. INV-H1-000065: expected 4163140, computed 4163135), which
is exactly the kind of "confidently wrong by a hair" result the brief warns
is worse than an honest flag. Fixed by routing every percentage adjustment
through a single `Decimal`-based half-up helper instead of ad hoc
arithmetic. *Residual risk for hospital 3*: none specific to this bug (it's
now the only rounding path in the codebase), but any *new* arithmetic added
later that bypasses that helper would reintroduce it invisibly.

**2. A reused key silently corrupts unrelated records downstream.** Two
invoice_ids are deliberately reused in hospital 1's own data
(INV-H1-000068, INV-H1-000152). A naive join of line items to invoices on
`invoice_id` fans every line under a reused id out across both header rows,
double-counting it in every cross-invoice running total that sequences
after it — cumulative volume discounts, threshold premiums, daily caps.
This produced small, hard-to-place dollar errors on *unrelated* invoices
much later in the sequence (e.g. INV-H1-000146, a `Preoperative
Immunologic Endoscopic Procedure` line came out 1 unit short of a
discount tier because an earlier duplicate had inflated the running total
by one). Fixed by deduplicating invoice headers before that join.
*Residual risk*: hospital 3 has its own reused ids (flagged in the
submission); if any other cross-invoice mechanism is added later without
going through the same deduplicated join, the same corruption reappears.

**3. Price-only matching accepts coincidences.** "Adv Renal Consultation"
is not a contracted service in hospital 1 at all, but it happened to bill
at exactly 3555.50 GBP — the unadjusted base rate of the totally unrelated
"Ambulatory Psychiatric Dialysis Session." An early matcher trusted any
unique (unit_basis, price) hit as a match; this one line was the single
false negative left after every other fix (INV-H1-000667). Fixed by
requiring a minimum text-plausibility score before trusting a price
anchor, at both the aggregate-description and per-line level. *Residual
risk*: this is a probabilistic fix, not a proof — a fabricated description
in any hospital that is *both* price-coincident *and* has a few tokens in
common with the wrong real service could still slip through. This is the
single mechanism I'd bet on if any hospital's `unknown_service` /
`unit_price_mismatch` predictions turn out to have errors, and I did
observe the exact same signature again in hospitals 2, 4 and 5 while
extending coverage: several fabricated descriptions billed at prices that
turned out to be the *exact* real rate of an unrelated contracted service
(e.g. two completely different fake hospital-4 descriptions both happened
to bill at 687.00 GBP and 3649.25 GBP, each the real rate of an unrelated
service) — caught correctly by this same guard, not a new fix.

**4. A directional contract clause read the wrong way silently
over-flags.** Section 10.1's exclusion-window clause ("measured in either
direction") describes how to measure the day-window around the excluding
service's date, not whether the restriction runs both ways between the two
named services. An initial implementation indexed it symmetrically, which
flagged a genuinely correct invoice (INV-H1-000830) as a violation. Fixed
by indexing the six exclusion pairs one-directionally, per the literal
"Service | Not billable within N days | Of this Service" table structure.
*Residual risk*: this was one clause out of many read under time pressure;
any other clause in hospital 3's base agreement that I read as symmetric
when it is one-directional (or vice versa) would fail the same way, and I
had no equivalent labelled example to check hospital 3's own exclusion
pairs against.

**5. A model built for one hospital's economics silently misprices another's.**
Hospital 5 is the only one of the five where facility and plan-tier
multipliers are real (every other hospital states them as 1.0). The
description matcher's price-anchoring had implicitly assumed one dominant,
stable price per description string — true when facility/tier are no-ops,
false the moment they aren't, since the *same* service at the *same*
hospital now legitimately bills at up to 9 different prices depending on
which of 3 facilities and 3 tiers is on the invoice. Before this was fixed,
hospital 5 flagged 26.1% of invoices, almost all spurious `unknown_service`
and `unit_price_mismatch`. Fixed by making the per-line price fallback
facility/tier-aware — it now adjusts a candidate service's rate by the
*specific* line's own facility code and plan tier before checking whether
the billed price matches. *Residual risk*: this is the newest, least-tested
part of the pricing logic; any hospital 5 line where facility, tier, *and*
a premium or discount all stack together is a plausible place for a
one-step-in-the-wrong-order bug to hide undetected, since I have no labels
to check the full 5-factor interaction against.

**6. Each hospital has its own abbreviation vocabulary; the matcher's
dictionary only knows what it's been shown.** Hospital 4 uses `supv` and
`vst` for "supervised" and "visit" — neither appears anywhere in hospital
1's data, so the dictionary built while developing against hospital 1
didn't have them, and hospital 4 initially came back with 75 spurious
`unknown_service` lines (13.7% of invoices flagged) that were genuinely
resolvable once those two entries were added (down to 8 lines, 7.5%
flagged). This isn't a one-time fix: it is a standing property of the
approach that a new hospital's vocabulary can silently degrade matching
until someone samples the actual unresolved strings and checks them by
hand against that hospital's own contract text, which is what I did each
time rather than assume the hospital-1 dictionary would generalize.

## What I'd do differently with another week

Everything in the failure-mode list above is a version of the same root
cause: a rule, or a matching assumption, that is *correct in the case it
was written against* but silently wrong on a case that case didn't happen
to exercise. With more time I would (a) hand-construct a handful of
synthetic edge-case invoices per rule — a cap violation with two lines
straddling the cap, a bundle where only one partner is late-cancelled, an
exclusion pair billed on the excluding side only, a hospital-5 line where
facility, tier and a premium all stack — to pressure-test rules no real
labelled example happens to cover, since that is exactly the gap every one
of the six failure modes above came from; and (b) extend the cheap,
contract-agnostic checks (duplicate IDs, date sanity, line/invoice
arithmetic) to hospital 1's own out-of-sample robustness and to any future
sixth hospital, since those need no service-catalogue work at all and are
pure upside within any time budget.
