"""Run the pricing engine against hospital 1 (the labelled dev set) and
report how well it does, per category, plus a confidence-calibration
table. This is the development loop the task asks us to use hospital 1
for; it is never part of the submitted predictions.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from audit.contract_h1 import build_contract
from audit.pricing import price_hospital


def main():
    inv = pd.read_csv(ROOT / "invoices" / "hospital_1_invoices.csv")
    li = pd.read_csv(ROOT / "invoices" / "hospital_1_line_items.csv")
    labels = pd.read_csv(ROOT / "labels" / "hospital_1_labels.csv")

    contract = build_contract()
    line_df, pred = price_hospital(contract, inv, li)

    merged = pred.merge(labels, on="invoice_id", how="inner", suffixes=("_pred", "_label"))

    tp = int(((merged["flagged"] == 1) & (merged["is_erroneous"] == 1)).sum())
    fp = int(((merged["flagged"] == 1) & (merged["is_erroneous"] == 0)).sum())
    fn = int(((merged["flagged"] == 0) & (merged["is_erroneous"] == 1)).sum())
    tn = int(((merged["flagged"] == 0) & (merged["is_erroneous"] == 0)).sum())
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")

    print(f"n invoices: {len(merged)}")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}")

    correct = merged[merged["is_erroneous"] == 0]
    exact_dollar_match = (correct["expected_total_cents_pred"] == correct["billed_total_cents"]).mean()
    print(f"\nAmong {len(correct)} correct (unflagged-truth) invoices:")
    print(f"  our expected_total exactly reproduces billed total: {exact_dollar_match:.1%}")
    print(f"  we (wrongly) flagged: {(correct['flagged']==1).mean():.1%}")

    print("\n--- False positives (we flagged a correct invoice) ---")
    fps = merged[(merged["flagged"] == 1) & (merged["is_erroneous"] == 0)]
    print(fps[["invoice_id", "error_category", "expected_total_cents_pred", "billed_total_cents", "confidence"]].head(20).to_string(index=False))

    print("\n--- False negatives (we missed an erroneous invoice) ---")
    fns = merged[(merged["flagged"] == 0) & (merged["is_erroneous"] == 1)]
    print(fns[["invoice_id", "error_categories", "expected_total_cents_label"]].head(20).to_string(index=False))

    print("\n--- Per label-category recall (among erroneous invoices we matched) ---")
    erroneous = merged[merged["is_erroneous"] == 1].copy()
    cat_rows = []
    for _, row in erroneous.iterrows():
        for cat in str(row["error_categories"]).split("|"):
            cat_rows.append({"category": cat, "caught": row["flagged"] == 1,
                              "dollar_exact": row["expected_total_cents_pred"] == row["expected_total_cents_label"]})
    cat_df = pd.DataFrame(cat_rows)
    summary = cat_df.groupby("category").agg(n=("caught", "size"), recall=("caught", "mean"), dollar_exact_rate=("dollar_exact", "mean"))
    print(summary.sort_values("n", ascending=False).to_string())

    print("\n--- Confidence calibration (flagged rows) ---")
    flagged = merged[merged["flagged"] == 1].copy()
    flagged["bucket"] = pd.cut(flagged["confidence"], bins=[0, 0.3, 0.5, 0.7, 0.85, 1.0])
    calib = flagged.groupby("bucket").agg(n=("is_erroneous", "size"), empirical_precision=("is_erroneous", "mean"))
    print(calib.to_string())

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    merged.to_csv(out_dir / "h1_calibration_detail.csv", index=False)
    print(f"\nWrote detail to {out_dir / 'h1_calibration_detail.csv'}")


if __name__ == "__main__":
    main()
