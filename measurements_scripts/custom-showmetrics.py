#!/usr/bin/env python3
import argparse
import csv
import math
import sys
from typing import List, Dict, Tuple

T_CRIT_95_DF9 = 2.262  # t_{0.975, df=9} for 95% CI with n=10 groups

def is_idle_row(values: List[float], eps: float = 0.0) -> bool:
    # Idle if ALL NF values are zero (or <= eps)
    return all(abs(v) <= eps for v in values)

def mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")

def sample_std(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))

def ci95_halfwidth(xs: List[float]) -> float:
    n = len(xs)
    if n <= 1:
        return 0.0
    s = sample_std(xs)
    return T_CRIT_95_DF9 * (s / math.sqrt(n))

def read_csv(path: str) -> Tuple[List[str], List[Dict[str, float]]]:
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")

        # Normalize column names
        raw_cols = reader.fieldnames
        cols = [c.replace("\ufeff", "").strip() for c in raw_cols]

        if "Time" not in cols:
            raise ValueError(
                f"Missing required column 'Time'. Found columns: {cols}"
            )

        nf_cols = [c for c in cols if c != "Time"]

        rows: List[Dict[str, float]] = []
        for r in reader:
            row: Dict[str, float] = {}
            for raw_c, norm_c in zip(raw_cols, cols):
                if norm_c == "Time":
                    continue
                try:
                    row[norm_c] = float(r[raw_c])
                except Exception as e:
                    raise ValueError(
                        f"Non-numeric value in column '{norm_c}': {r[raw_c]!r}"
                    ) from e
            rows.append(row)

        return nf_cols, rows

def split_groups(nf_cols: List[str], rows: List[Dict[str, float]], eps: float):
    groups: List[List[Dict[str, float]]] = []
    current: List[Dict[str, float]] = []

    for row in rows:
        values = [row[c] for c in nf_cols]
        if is_idle_row(values, eps):
            if current:
                groups.append(current)
                current = []
        else:
            current.append(row)

    if current:
        groups.append(current)

    return groups

def group_stats(nf_cols: List[str], group_rows: List[Dict[str, float]]):
    stats: Dict[str, Dict[str, float]] = {}

    for c in nf_cols:
        xs = [r[c] for r in group_rows]
        stats[c] = {
            "min": min(xs),
            "max": max(xs),
            "avg": mean(xs),
        }

    return stats

def aggregate_over_groups(nf_cols: List[str], per_group_stats):
    agg: Dict[str, Dict[str, float]] = {}

    for c in nf_cols:
        samples = [g[c]["avg"] for g in per_group_stats]
        agg[c] = {
            "mean": mean(samples),
            "ci95_pm": ci95_halfwidth(samples),
            "min": min(samples),
            "max": max(samples),
        }

    return agg

def main():
    ap = argparse.ArgumentParser(
        description="Analyze per-NF power consumption across non-idle groups."
    )
    ap.add_argument("csv_path", help="Input CSV path")
    ap.add_argument("--eps", type=float, default=0.0, help="Zero tolerance")
    ap.add_argument("--expected-groups", type=int, default=10, help="Expected number of groups")
    ap.add_argument("--unit", choices=["W", "mW"], default="W", help="Output unit")

    args = ap.parse_args()

    nf_cols, rows = read_csv(args.csv_path)

    scale = 1000.0 if args.unit == "mW" else 1.0
    unit_label = args.unit

    if scale != 1.0:
        for row in rows:
            for c in nf_cols:
                row[c] *= scale

    eps_scaled = args.eps * scale

    groups = split_groups(nf_cols, rows, eps_scaled)

    if len(groups) != args.expected_groups:
        print(
            f"[!] ERROR: found {len(groups)} non-idle groups, expected {args.expected_groups}.",
            file=sys.stderr,
        )
        sys.exit(2)

    per_group = []
    for i, g in enumerate(groups, start=1):
        st = group_stats(nf_cols, g)
        per_group.append(st)

        print(f"\n=== Group {i} (rows={len(g)}) ===")
        for c in nf_cols:
            print(
                f"{c:>6}  avg={st[c]['avg']:.6f} {unit_label}  "
                f"min={st[c]['min']:.6f} {unit_label}  "
                f"max={st[c]['max']:.6f} {unit_label}"
            )

    agg = aggregate_over_groups(nf_cols, per_group)

    print(f"\n=== Aggregated over {args.expected_groups} groups ===")
    for c in nf_cols:
        a = agg[c]
        print(
            f"{c:>6}  mean={a['mean']:.6f} {unit_label}  "
            f"CI95=±{a['ci95_pm']:.6f} {unit_label}  "
            f"min={a['min']:.6f} {unit_label}  "
            f"max={a['max']:.6f} {unit_label}"
        )
    
    # final_line = ", ".join(
    #     f"{agg[c]['mean']:.4f} ± {agg[c]['ci95_pm']:.3f}"
    #     for c in nf_cols
    # )
    # print("\n"+final_line)

if __name__ == "__main__":
    main()
