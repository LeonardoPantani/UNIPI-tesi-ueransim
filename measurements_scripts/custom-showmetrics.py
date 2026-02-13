#!/usr/bin/env python3
import argparse
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class Summary:
    mean: float
    ci95_half: float


def load_csv(path: str) -> tuple[list[str], np.ndarray]:
    df = pd.read_csv(path)
    df.columns = [c.replace("\ufeff", "").strip() for c in df.columns]

    if "Time" not in df.columns:
        raise ValueError(f"Missing required column 'Time'. Found columns: {list(df.columns)}")

    nf_cols = [c for c in df.columns if c != "Time"]
    if not nf_cols:
        raise ValueError("No NF columns found (only 'Time' present).")

    try:
        X = df[nf_cols].astype(float).to_numpy()
    except Exception as e:
        raise ValueError("Non-numeric value found in NF columns.") from e

    return nf_cols, X


def split_groups(X: np.ndarray) -> list[np.ndarray]:
    idle = np.all(np.abs(X) <= 0, axis=1)

    groups: list[np.ndarray] = []
    start = None

    for i, is_idle in enumerate(idle):
        if not is_idle and start is None:
            start = i
        elif is_idle and start is not None:
            g = X[start:i]
            if g.size:
                groups.append(g)
            start = None

    if start is not None:
        g = X[start:]
        if g.size:
            groups.append(g)

    return groups


def group_mean(group: np.ndarray) -> np.ndarray:
    return np.mean(group, axis=0)


def t_ci95_halfwidth(samples: np.ndarray) -> float:
    n = samples.shape[0]
    if n <= 1:
        return 0.0
    s = np.std(samples, ddof=1)
    tcrit = stats.t.ppf(0.975, df=n - 1)
    return float(tcrit * (s / np.sqrt(n)))


def aggregate_over_groups(group_means: np.ndarray) -> Summary:
    m = float(np.mean(group_means))
    ci = t_ci95_halfwidth(group_means)
    return Summary(mean=m, ci95_half=ci)


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze per-NF power consumption across non-idle groups.")
    ap.add_argument("csv_path", help="Input CSV path")
    ap.add_argument("--expected-groups", type=int, default=10, help="Expected number of groups")
    args = ap.parse_args()

    nf_cols, X = load_csv(args.csv_path)

    X = X * 1000.0 # to mW

    groups = split_groups(X)

    if len(groups) != args.expected_groups:
        print(
            f"[!] ERROR: found {len(groups)} non-idle groups, expected {args.expected_groups}.",
            file=sys.stderr,
        )
        return 2

    per_group_means = []

    for i, g in enumerate(groups, start=1):
        gavg = group_mean(g)
        per_group_means.append(gavg)

        # print(f"\n=== Group {i} (rows={g.shape[0]}) ===")
        # for name, a in zip(nf_cols, gavg):
        #     print(f"{name:>6}  avg={a:.6f} {args.unit}")

    per_group_means = np.vstack(per_group_means)
    print(f"\n=== Aggregated ===")

    for j, name in enumerate(nf_cols):
        s = aggregate_over_groups(per_group_means[:, j])
        print(
            f"{name:>6}  mean={s.mean:.6f} mW   "
            f" CI95=±{s.ci95_half:.6f} mW"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
