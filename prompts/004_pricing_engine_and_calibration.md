# 004 — Pricing engine, then calibrate against hospital 1 until it's clean

> Before writing the pricing engine: the contract text has at least two
> genuinely ambiguous points (whether a threshold premium uplifts the whole
> day's quantity or just the excess; what "expected" quantity means once a
> daily cap is exceeded). Don't guess — hospital 1 is labelled specifically
> so we can settle this empirically. Find a single-category labelled
> example for each ambiguity, work out by hand what assumption reproduces
> the label's expected_total_cents exactly, and only then write the rule.

This resolved both ambiguities against real examples (INV-H1-000348 for the
premium scope; INV-H1-000015 and INV-H1-000725 for the cap, which is where
it became clear the pre-corruption quantity isn't recoverable at all — see
the decision log) before a single line of `audit/pricing.py` existed.

> Now write the pricing engine against the Contract model: bundle
> substitution, then premium/uplift, then cumulative discount, in the order
> the contracts specify, plus the cross-invoice mechanisms (duplicate
> service same day, exclusion windows) that need the whole hospital's line
> items rather than one invoice at a time. Then write a calibration script
> that runs it against all of hospital 1, joins to the labels, and reports
> precision/recall, per-category recall, per-category dollar-exact-match
> rate, and a confidence-calibration table. Don't hand me a summary — show
> me the false positives and false negatives directly so we can look at the
> actual invoice.

First run: precision 0.187 (TP=57, FP=248). Told Claude:

> That's not a matching problem, that's a systematic few-cents-off pattern
> — look at the actual false positive diffs before touching the matcher.

The diffs were consistently tiny (e.g. 5 cents on a discounted line), which
pointed straight at rounding mode: the code used Python's bare `round()`
(banker's rounding) instead of the contract's stated half-up rule. One-line
category of fix (route every adjustment through a `Decimal`-based half-up
helper) took precision to 0.814 (FP=13).

> Good, now go through the remaining 13 false positives one at a time —
> don't fix all of them blind, tell me what each one's root cause is first.

That pass found, in order:
1. `cross_invoice_duplicate` flagged on *both* sides of a genuine duplicate
   pair instead of only the later one (INV-H1-000079 vs. the real duplicate
   INV-H1-000231) — fixed by only flagging the non-minimal line_id in the
   group.
2. Six `unit_price_mismatch` false positives, all on services with
   cumulative-volume discounts, all *deeper-tier* discounts applied one or
   two units too early — traced to invoice_ids `INV-H1-000068` and
   `INV-H1-000152` being genuinely reused in the source data, which fans
   line items out across both header rows on a naive join and double-counts
   them in the running discount totals for everything that sequences after.
   Fixed by deduplicating the invoice header before that join, and gave
   `duplicate_invoice_id` invoices their own "don't attempt to reprice, flag
   the collision, report the billed total" path rather than let them corrupt
   unrelated invoices.
3. One `exclusion_window_violation` false positive (INV-H1-000830) — the
   exclusion pairing had been indexed symmetrically; re-reading Section
   10.1 against this specific counterexample showed the rule is
   one-directional (see decision log).

That round took the result to precision 1.000, recall 0.983 (one remaining
false negative, INV-H1-000667, `unknown_service`).

> One miss left — show me that invoice's lines and what each one resolved
> to.

The miss was a coincidental price match: "Adv Renal Consultation" (not a
real contracted service) happened to bill at exactly the base rate of an
unrelated real service. Added a minimum text-plausibility guard before
trusting any price-only match, at both the aggregate and per-line level.
That reached precision 1.000 / recall 1.000 on hospital 1 (`n=913`,
TP=58, FP=0, FN=0).

> Now run it on hospital 3 and sanity-check the *rate* of flags, not just
> whether it runs. If hospital 1's true error rate is ~6%, hospital 3
> shouldn't come out anywhere near 30%.

First hospital-3 run flagged 29.7% of invoices, dominated by
`wrong_unit_basis` (102) and `unknown_service` (82). Traced `wrong_unit_basis`
to a modelling bug specific to hospital 3's one dual-unit-basis service
("Focused Urologic Telemetry Monitoring", billed as the single combined
code `per_hour_per_item`): the contract reader had split it into two
single-basis `RateEntry` objects instead of one entry accepting both, so
lookup returned an arbitrary one and flagged the other as wrong. Fixing
that (one RateEntry per rate row, carrying every accepted basis together)
took the flag rate to 18.0%.

> unknown_service is still 10x hospital 1's rate — before assuming that's
> real, sample the actual unresolved descriptions and look for a pattern.

Two findings from that sample: (a) several descriptions were genuinely
missing their specialty word and resolvable once the matcher was allowed to
check a candidate's *premium/discount-adjusted* prices, not just its base
rate, when picking between text-scoring candidates (took unknown_service
from 82 to 36); (b) the remaining ones shared suspicious *exact* prices
across otherwise-unrelated service names (several different fake
descriptions all billing at exactly 163525 cents, or 49050 cents) — a
strong signal of deliberately fabricated placeholder services rather than a
matcher gap, so left as low-confidence `unknown_service` rather than forced
to resolve. Final hospital-3 flag rate: 10.9%, with a mean confidence of
0.62 on flagged rows vs. 0.90 on unflagged.
