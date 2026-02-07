#!/usr/bin/env python3

import argparse
import re
from pathlib import Path
import sys
import numpy as np
from sklearn.utils import resample
from sklearn.linear_model import HuberRegressor

# Omatch measurement lines like: "imsi-...: 123 ms"
MEAS_RE = re.compile(r"^\s*imsi-[^:]+:\s*([0-9]+(?:\.[0-9]+)?)\s*ms\s*$", re.IGNORECASE)


def parse_measurements_ms(text: str) -> np.ndarray:
    vals = []
    for line in text.splitlines():
        m = MEAS_RE.match(line)
        if m:
            vals.append(float(m.group(1)))
    return np.asarray(vals, dtype=float)


def huber_location(x: np.ndarray) -> float:
    """
    Robust location estimate using HuberRegressor with intercept-only model:
      y_i = mu + e_i
    Returns mu.
    """
    if x.size == 0:
        return float("nan")

    X = np.zeros((x.size, 1), dtype=float)

    model = HuberRegressor(
        epsilon=1.35,
        alpha=0.0,
        fit_intercept=True,
        max_iter=200
    )
    model.fit(X, x)
    return float(model.intercept_)


def bootstrap_ci95_huber(x: np.ndarray, n_boot: int = 5000, seed: int = 123) -> tuple[float, float, float]:
    """
    Percentile bootstrap CI95 for Huber location.
    Returns (stat, ci_low, ci_high).
    """
    if x.size == 0:
        return (float("nan"), float("nan"), float("nan"))

    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=float)

    for i in range(n_boot):
        xb = resample(
            x,
            replace=True,
            n_samples=x.size,
            random_state=int(rng.integers(0, 2**31 - 1))
            )
        xb = np.asarray(xb, dtype=float)
        stats[i] = huber_location(xb)

    stat = huber_location(x)
    low = float(np.quantile(stats, 0.025))
    high = float(np.quantile(stats, 0.975))
    return (float(stat), low, high)


def fmt_ms(v: float) -> str:
    if np.isnan(v):
        return "N/A"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))} ms"
    return f"{v:.2f} ms"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compute Huber statistics and 95% CI from UE registration logs."
    )
    ap.add_argument("input", help="Path to a .txt file.")
    ap.add_argument("--drop-first", type=int, default=0, help="Drop the first N measurements after parsing (default: 0).")
    ap.add_argument("--drop-last", type=int, default=0, help="Drop the last N measurements after parsing (default: 0).")
    args = ap.parse_args()

    path = Path(args.input)

    if not path.exists() or not path.is_file():
        print(f"ERROR: Input file not found: {path}", file=sys.stderr)
        return 2

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"ERROR: Cannot read file: {path} ({e})", file=sys.stderr)
        return 2

    x_all = parse_measurements_ms(text)

    if x_all.size == 0:
        print("ERROR: No measurement lines found. Expected lines like:", file=sys.stderr)
        print("  imsi-001010000000001: 3058 ms", file=sys.stderr)
        return 3

    drop_first = max(0, int(args.drop_first))
    drop_last = max(0, int(args.drop_last))

    n = x_all.size
    start = drop_first
    end = n - drop_last

    if start >= end:
        print(
            f"ERROR: After dropping first={drop_first} and last={drop_last}, no data remains "
            f"(parsed={n}).",
            file=sys.stderr
        )
        return 3

    x = x_all[start:end]

    # descriptive stats on the filtered data
    x_min = float(np.min(x))
    x_max = float(np.max(x))
    x_mean = float(np.mean(x))
    x_median = float(np.median(x))

    # estimate CI95
    hub, lo, hi = bootstrap_ci95_huber(x, n_boot=5000, seed=123)
    half = (hi - lo) / 2.0

    print("-" * 80)
    print(f"File: {path}")
    print(f"Parsed measurements: {n}")
    print(f"After drop-first={drop_first}, drop-last={drop_last}: n={x.size}")
    print()
    print("Descriptive statistics (after drops):")
    print(f"  min:    {fmt_ms(x_min)}")
    print(f"  max:    {fmt_ms(x_max)}")
    print(f"  mean:   {fmt_ms(x_mean)}")
    print(f"  median: {fmt_ms(x_median)}")
    print()
    print("Estimate (Huber) + 95% CI:")
    print(f"  huber:  {fmt_ms(hub)} ± {fmt_ms(half)}   (CI95: [{fmt_ms(lo)}, {fmt_ms(hi)}])")
    print(f"{hub},{half}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
