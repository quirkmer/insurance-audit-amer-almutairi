# Write-up

## What I did

I developed and calibrated against hospital 1 (labelled, not submitted) and
submitted predictions for hospitals 2, 3, 4 and 5. I went deep on hospital 3
first — it has a mid-term amendment that reprices several services and adds
new ones from a specific date, the part of this exercise most likely to
expose a real mistake, compared to hospital 4's static rate table — then
extended the same engine to the other three once that was solid.

I used Claude for essentially all of the code. My own time went into
deciding what to check, reading the contracts closely enough to judge
whether the output was right, and tracking down specific invoices by hand
whenever a number didn't reconcile. The `prompts/` folder has the actual
sequence of that back-and-forth; I'm not going to repeat it here, but the
short version is that almost every fix in this submission came from me
refusing to accept "looks about right."

## How I measured results

Hospital 1 has labels, so I could score directly: run the engine, compare
to the labels, and check not just whether an invoice was flagged but
whether my recomputed total matched exactly. That stricter check is what
actually found bugs — a rounding mismatch, a data-handling issue with a
reused invoice ID, and one wrong match caused by a coincidental price
overlap. After fixing those, I had zero disagreements with the labels on
hospital 1's 58 flagged invoices out of 913.

I don't want to oversell that number — it's a small sample, and it's the
exact one I was debugging against, so it mainly tells me I've fixed what
hospital 1 happens to expose, not that there's nothing left to find.
Hospitals 2, 3, 4 and 5 have no labels at all, so I could only sanity-check
them indirectly: does the flag rate land near hospital 1's known ~6%? Two
of them didn't at first — hospital 4 came back at 14%, hospital 5 at 26% —
and both turned out to be real, checkable bugs rather than a genuine
difference in error rate (a missing abbreviation in one case, a pricing
rule I hadn't accounted for in the other). Once fixed, all four settled
into the same 6.8-7.5% band. That's a weaker guarantee than hospital 1's
numbers, but it's more than nothing, and I'd rather report the before/after
than just the number I ended on.

## Where I'm uncertain

Two points in the contracts can't be resolved from the text alone, and I
only settled them by finding a labelled example that ruled one reading
out: whether a quantity-threshold surcharge applies to a whole day's
units or just the excess, and what quantity a daily cap violation was
"supposed" to be. For the cap case, I concluded the true original
quantity isn't recoverable at all, even in principle, so I use the most
defensible convention and flag those rows with lower confidence
specifically on the dollar figure.

More generally, my confidence is lower wherever a claim depends on
correctly identifying which service a vague description refers to, and
higher wherever it's pure arithmetic or a hard fact like a duplicated ID.
The place I'd expect a remaining mistake, if there is one, is hospital 5:
it's the only one of the five where price genuinely depends on which
facility and which plan tier is on the invoice, and it's the newest part
of the pipeline with no labelled example to check the full interaction
against once a facility adjustment, a plan-tier adjustment, and a premium
or discount all land on the same line.

## What I'd do with another week

Build my own test cases instead of only reacting to what hospital 1
happens to contain, since every bug I actually found came from a real
labelled example, and the cases it doesn't cover — a cap violation split
across two lines, a hospital-5 line where facility, tier and a premium all
stack at once — are exactly what I currently have no way to check. I'd
also go back through hospital 1 itself looking for edge cases the labels
happen not to exercise, rather than assume a clean scorecard there means
the underlying rules are complete.
