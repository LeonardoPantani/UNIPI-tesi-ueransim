#!/usr/bin/env python3
import re
import statistics
import matplotlib.pyplot as plt
import sys
import os
import numpy as np

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <file1> [file2 ...]")
    sys.exit(1)

files = [f for f in sys.argv[1:] if os.path.isfile(f)]

if not files:
    print("No valid file found.")
    sys.exit(0)

iter_re = re.compile(r"Iteration (\d+):")
val_re = re.compile(r":\s+(\d+)\sms")
alg_re = re.compile(r"ALG_TYPE\s*:\s*(\S+)")
ciph_re = re.compile(r"DEF_CIPH\s*:\s*(\S+)")
ue_re = re.compile(r"UEs per batch\s*:\s*(\d+)")

plt.figure(figsize=(10,6))
ue_batch = None
all_averages = []
labels = []

for f in files:
    with open(f, "r") as fh:
        lines = fh.readlines()

    alg, ciph = "unknown", "unknown"
    for line in lines:
        if "ALG_TYPE" in line:
            m = alg_re.search(line)
            if m:
                alg = m.group(1)
        elif "DEF_CIPH" in line:
            m = ciph_re.search(line)
            if m:
                ciph = m.group(1)
        elif "UEs per batch" in line:
            m = ue_re.search(line)
            if m:
                ue_batch = m.group(1)

    label = f"{alg} / {ciph}"
    iterations, averages = [], []
    i = 0
    while i < len(lines):
        m = iter_re.match(lines[i])
        if m:
            it_num = int(m.group(1))
            i += 1
            vals = []
            while i < len(lines) and not iter_re.match(lines[i]):
                v = val_re.search(lines[i])
                if v:
                    vals.append(int(v.group(1)))
                i += 1
            if vals:
                iterations.append(it_num)
                averages.append(statistics.mean(vals))
        else:
            i += 1

    plt.plot(iterations, averages, linewidth=1.5, label=label)
    if averages:
        all_averages.append(averages)
        labels.append(label)

if ue_batch is None:
    ue_batch = "?"

plt.title(f"Average UE registration time per iteration ({ue_batch} UEs batch)")
plt.xlabel("Iteration no.")
plt.ylabel("Time average [ms]")
plt.grid(True, linestyle="--", alpha=0.6)
plt.legend(title="Key exchange algorithm", fontsize=10)
plt.tight_layout()
plt.show()

# --- secondo grafico: box plot ---
if all_averages:
    plt.figure(figsize=(8,6))
    box = plt.boxplot(all_averages, patch_artist=True, tick_labels=labels)
    cmap = plt.cm.tab10 # type: ignore
    for i, patch in enumerate(box['boxes']):
        patch.set_facecolor(cmap(i % 10))
        patch.set_alpha(0.8)
    plt.title(f"Distribution of UE registration times ({ue_batch} UEs batch)")
    plt.ylabel("Average time [ms]")
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.xticks(rotation=20)
    plt.tight_layout()
    plt.show()
