"""Run the pricing engine against hospitals 2, 3, 4 and 5 and write the
combined submission.csv (hospital 1 is the labelled dev set only, per the
brief, and is not submitted).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.run_hospital import run


def main():
    frames = [run(key) for key in ("h2", "h3", "h4", "h5")]
    combined = pd.concat(frames, ignore_index=True).sort_values("invoice_id").reset_index(drop=True)
    combined.to_csv(ROOT / "submission.csv", index=False)

    print("=== combined ===")
    print(f"n invoices: {len(combined)}")
    print(f"flagged: {(combined['flagged']==1).sum()} ({(combined['flagged']==1).mean():.1%})")
    print(f"Wrote {ROOT / 'submission.csv'}")


if __name__ == "__main__":
    main()
