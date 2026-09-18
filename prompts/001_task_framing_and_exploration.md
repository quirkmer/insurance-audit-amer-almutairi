# 001 — Task framing and exploration

> I have a technical assessment: a health insurer audits invoices from five
> hospitals against five different contracts. Hospital 1 is labelled, the
> rest aren't. I need to find pricing/billing errors and submit predictions
> in a fixed template, plus a decision log, an evaluation report, and a
> prompts log. Budget is 6-8 hours; I've decided to go deep on hospital 1
> (for calibration) plus one of hospital 3 or 4, and skip the rest given the
> time budget. Read the README, the submission template, hospital 1's
> contract and labels, and hospital 3 and 4's contracts, and tell me which
> of 3/4 is the better pick before we write any code — I want a reason, not
> just a coin flip.

Claude read `README.md`, `submission_template.csv`, `contracts/hospital_1/`,
`labels/hospital_1_labels.csv`, and both `contracts/hospital_3/` and
`contracts/hospital_4/` in full, then also pulled a sample of the invoice and
line-item CSVs/JSONL to see the actual shape of the billing descriptions
(abbreviated, reordered, sometimes carrying an internal code like
`/NG-3022`) before recommending hospital 3: it's the contract with a
mid-term amendment that repriced seven services and added two new ones
by *service date*, which is a structurally different problem from a static
rate table (hospital 4 is single-document and fully static, so it would
exercise the same rules engine without adding new risk to retire).

Also established at this stage, by direct inspection of the CSVs rather than
assumption:

- Money is always integer cents; there is a `unit_basis_as_billed` column
  distinct from the contract's own unit-basis wording.
- The same contracted service shows up under many different abbreviated
  descriptions (e.g. "Ambulatory Pulmonary Recovery Room Occupancy" as
  "Amb Pulm Recov Rm Occupancy", "Ambulatory Pulm Recovery Room Occupancy",
  and with a trailing `/NG-9869` code) — confirming the brief's own warning
  that description-to-service resolution is real work, not a lookup.
- A `/NG-####` suffix is stable per exact description string but isn't an
  independent signal (it's 1:1 with the description text itself), so it's
  noise to strip, not a hidden key to exploit.
