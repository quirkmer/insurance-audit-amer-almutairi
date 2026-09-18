"""End-to-end regression test: the whole pricing engine against the
labelled hospital 1 dev set. This is the test that matters most -- it is
what caught the rounding bug (bare round() vs. half-up), the duplicate
invoice-id fan-out bug, the reversed exclusion-window direction, and the
coincidental price-collision false match, all listed in the decision log.
"""
from pathlib import Path

import pandas as pd

from audit.contract_h1 import build_contract
from audit.pricing import price_hospital

ROOT = Path(__file__).resolve().parent.parent


def _run():
    inv = pd.read_csv(ROOT / "invoices" / "hospital_1_invoices.csv")
    li = pd.read_csv(ROOT / "invoices" / "hospital_1_line_items.csv")
    labels = pd.read_csv(ROOT / "labels" / "hospital_1_labels.csv")
    contract = build_contract()
    _, pred = price_hospital(contract, inv, li)
    return pred.merge(labels, on="invoice_id", how="inner", suffixes=("", "_label"))


def test_perfect_flag_agreement_on_hospital_1():
    merged = _run()
    tp = ((merged["flagged"] == 1) & (merged["is_erroneous"] == 1)).sum()
    fp = ((merged["flagged"] == 1) & (merged["is_erroneous"] == 0)).sum()
    fn = ((merged["flagged"] == 0) & (merged["is_erroneous"] == 1)).sum()
    assert fp == 0, f"{fp} correct invoices wrongly flagged"
    assert fn == 0, f"{fn} erroneous invoices missed"
    assert tp == int(merged["is_erroneous"].sum())


def test_correct_invoices_reproduce_billed_total_exactly():
    merged = _run()
    correct = merged[merged["is_erroneous"] == 0]
    mismatches = correct[correct["expected_total_cents"] != correct["billed_total_cents"]]
    assert len(mismatches) == 0, mismatches[["invoice_id", "expected_total_cents", "billed_total_cents"]]


def test_flagged_confidence_is_lower_than_unflagged_confidence():
    # Not a hard business rule, just a sanity check on the intent: a flagged
    # (uncertain-by-definition) row should not, on average, claim more
    # confidence than a row we believe is simply correct.
    merged = _run()
    assert merged[merged["flagged"] == 1]["confidence"].mean() < merged[merged["flagged"] == 0]["confidence"].mean()
