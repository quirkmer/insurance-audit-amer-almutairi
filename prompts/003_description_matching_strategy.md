# 003 — Resolving descriptions to contracted services

> The line-item description is free text and doesn't match the contract
> wording — I've seen "Procedure Routine Urologic Biop" for "Routine
> Urologic Biopsy Procedure" and similar abbreviations throughout. Before
> writing a matcher: check whether the billed unit_price, for the
> *unadjusted* case, just equals a known contracted rate often enough to
   use price as the primary signal, with text as a fallback/tiebreaker
> rather than the other way round. Group hospital 1's line items by
> distinct description string first — there should be far fewer distinct
> strings than line items — and tell me the split between "price alone
> resolves it" and "needs text" before you build the text side.

That check (run against hospital 1) came back 393/488 distinct descriptions
resolved by an exact (unit_basis, price) match to a unique contracted rate,
81 needing text, 14 unresolved. That ratio is what justified the two-tier
design in `audit/matching.py`: price-anchor first (checking every rate
window, since hospital 3's rates move on 1 Jan 2025), text second.

> For the text side, don't try to build a general abbreviation model —
> look at the actual unresolved/text-tier description strings by hand and
> tell me what abbreviation pattern each one is (truncation vs. real
> clinical shorthand like ENT/MSK/GI) before writing the dictionary.

This produced the `IRREGULAR_ABBREVIATIONS` dictionary — built by reading
the specific vocabulary in the data, not guessed in the abstract — plus a
plain prefix-match fallback for ordinary truncation ("Interm" / "Intermittent"),
since most abbreviation in this dataset turned out to be simple truncation
and only a handful of tokens (`ent`, `msk`, `gi`, `cr`, `rtn`, `asst`, ...)
needed an explicit mapping.

Iteration on this dictionary continued through the hospital-3 run in
`004_pricing_engine_and_calibration.md` — several tokens (`thtr`, `tm`,
`spcm`, `anly`, `immun`, `inpt`) were added only after they showed up as
unresolved in real data, not pre-emptively.
