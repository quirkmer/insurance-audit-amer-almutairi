# 002 — How to encode the contracts

> Don't hand-transcribe the rate tables into Python dicts — that's exactly
> the kind of step where a typo becomes a silent, confident error, and
> there's no way to re-check it against the source later. Write a generic
> Markdown table parser and a small per-hospital "reader" that assigns
> meaning to each table's columns, so the actual numbers always come from
> the .md file, not from my transcription of it. Build a shared `Contract`
> data model (rates, premiums, weekend uplifts, volume discounts, caps,
> bundles, exclusion windows) that the pricing engine can be written against
> without knowing anything about Markdown or which hospital it came from —
> hospital 3's amendment should just be "a service can have more than one
> rate entry with different effective date windows," not a special case in
> the pricing logic.

This produced `audit/md_tables.py` (generic pipe-table extraction),
`audit/money.py` (GBP/percentage/rounding parsing, half-up cents), and
`audit/model.py` (`Contract` + typed rule dataclasses), then
`audit/contract_h1.py` and `audit/contract_h3.py` as the hospital-specific
assembly step.

One real bug from this phase, caught by a sanity check rather than by the
labels: the table-lookup helper originally matched a table by "contains
these headers", not "these headers exactly." Hospital 3's Base Agreement has
*two* tables that both contain a plain "Uplift" column (threshold premiums
and weekend uplifts) — the loose match silently grabbed the wrong table for
one of them. Fixed by requiring an exact header-set match, and wrote a test
for it (`tests/test_contracts.py`).
