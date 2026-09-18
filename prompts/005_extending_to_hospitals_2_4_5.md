# 005 — Extending coverage to hospitals 2, 4 and 5

After the hospital 3 submission was calibrated and finished, the scope was
deliberately widened:

> Now finish all the rest of the hospitals in one go, except for hospital 1.

Before writing anything, Claude read hospitals 2, 4 and 5's contracts in
full (4 had been skimmed earlier when choosing between 3 and 4, but not
read end-to-end) and flagged the two structural differences up front,
before touching code: hospital 5 has real facility- and plan-tier
multiplier tables (every other hospital's are stated as 1.0), and hospital
2 has no tables at all — its rates are stated one-per-paragraph in prose,
interleaved with roughly a dozen Articles of pure boilerplate.

For hospital 4 and 5, both tabular, the existing generic table-reader
pattern applied directly, with two hospital-specific column-order gotchas
caught by reading the actual header row rather than assuming it matched
hospital 1/3's shape (documented in the decision log and locked in with
tests: `test_h4_bundle_table_column_order_is_read_correctly`,
`test_h4_base_rate_table_has_no_cap_column_caps_come_from_section_6`).

For hospital 5's real multipliers, this was treated as a model change, not
just a new reader: `Contract` gained `facility_multiplier()` /
`plan_tier_multiplier()`, defaulting to a genuine 1.0 no-op so hospitals
1-4 are provably unaffected (locked in by
`test_h5_is_the_only_contract_with_real_multipliers`).

For hospital 2's prose, Claude proposed (and this was accepted without
back-and-forth, since the reasoning was sound on inspection): rather than
force a table-shaped parser onto prose, write a regex-based reader that
finds every paragraph matching "N.N In respect of..." and extracts each
optional mechanic (weekend uplift, threshold premium, cap, one-or-two-tier
discount, bundle, exclusion) via its own independently-searched sentence
pattern, so the order clauses happen to appear in within a paragraph
doesn't matter. The boilerplate Articles between the service paragraphs
were confirmed, by actually reading a sample of them, to carry zero pricing
content before deciding it was safe to skip them by construction rather
than explicitly filter them out.

Running each hospital for the first time surfaced two real bugs, found the
same way the hospital-1 bugs were found — not by inspection, but by
noticing the aggregate flag rate was implausible and tracing why:

- Hospital 4 came back at 13.7% flagged (vs. hospital 1's known ~6%).
  Sampling the actual unresolved descriptions showed two missing
  abbreviations (`supv`, `vst`) specific to hospital 4's own billing
  vocabulary — not present anywhere in hospital 1's data, so the
  dictionary built against hospital 1 simply hadn't seen them. Adding
  them brought hospital 4 to 7.5%.
- Hospital 5 came back at 26.1% flagged. This one was not a vocabulary
  gap: the price-anchoring matcher had implicitly assumed one stable price
  per description, which breaks the moment facility and plan-tier
  multipliers are real, since the same service now legitimately bills at
  up to 9 different prices depending on which facility and tier are on the
  invoice. Fixed by making the per-line price-matching fallback take the
  specific line's own facility code and plan tier into account before
  checking candidate prices. Brought hospital 5 to 7.2%.

After both fixes, all four submitted hospitals converged to 6.8-7.5%
flagged, next to each other and next to hospital 1's true rate — reported
in the evaluation report as a before/after, not just the final number,
since the "it looked wrong so I found out why" step is the actual evidence
of correctness, not the number itself.

Two documents were then updated to keep them honest about the new scope:
`WRITEUP.md`, which had said "I didn't get to hospitals 2, 4, or 5" (true
of the first pass, false once this one landed), and the decision log's
scope section. Both were rewritten rather than left inconsistent with what
was actually submitted.
