"""Run the pricing engine against any one of hospitals 2, 3, 4, 5 and write
that hospital's predictions to reports/<hospital>_predictions.csv, plus a
line-level detail CSV for inspection.

Usage: python scripts/run_hospital.py h2
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from audit.pricing import price_hospital

READERS = {}
for _mod in ("h2", "h3", "h4", "h5"):
    module = __import__(f"audit.contract_{_mod}", fromlist=["build_contract"])
    READERS[_mod] = module.build_contract


def run(hospital_key: str) -> pd.DataFrame:
    n = hospital_key[1]
    inv = pd.read_csv(ROOT / "invoices" / f"hospital_{n}_invoices.csv")
    li = pd.read_csv(ROOT / "invoices" / f"hospital_{n}_line_items.csv")

    contract = READERS[hospital_key]()
    line_df, pred = price_hospital(contract, inv, li)

    template_cols = ["invoice_id", "flagged", "error_category", "expected_total_cents", "billed_total_cents", "confidence"]
    pred = pred[template_cols].sort_values("invoice_id").reset_index(drop=True)

    (ROOT / "reports").mkdir(exist_ok=True)
    pred.to_csv(ROOT / "reports" / f"hospital_{n}_predictions.csv", index=False)
    line_df.to_csv(ROOT / "reports" / f"hospital_{n}_line_detail.csv", index=False)

    print(f"=== hospital {n} ===")
    print(f"n invoices: {len(pred)}")
    print(f"flagged: {(pred['flagged']==1).sum()} ({(pred['flagged']==1).mean():.1%})")
    print("confidence (flagged vs unflagged mean):",
          round(pred.loc[pred.flagged==1, 'confidence'].mean(), 3) if (pred.flagged==1).any() else None,
          round(pred.loc[pred.flagged==0, 'confidence'].mean(), 3) if (pred.flagged==0).any() else None)
    print("top categories:")
    print(pred[pred["flagged"] == 1]["error_category"].value_counts().head(15))
    print()
    return pred


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "h2"
    run(key)
