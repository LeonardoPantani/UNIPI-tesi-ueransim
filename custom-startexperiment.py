#!/usr/bin/env python3
import subprocess
import re
import time
import statistics
import signal
import select
import os
import sys
from datetime import datetime
from tqdm import tqdm

# edit this
CMD_BASE = ["sudo", "-n", "./build/nr-ue", "-i", "imsi-001010000000001", "-c", "config/ue-1.yaml"]
N_UE = 1 # closed_loop: 10
ITERATIONS = 100 # closed_loop: 100
TIMEOUT = 5 # closed_loop: 5
DELAY = 1 # closed_loop: 3
DEF_CIPH = "TLS_AES_256_GCM_SHA384" # TLS_AES_256_GCM_SHA384
# stop editing

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} <single|closedloop> <ALG_TYPE>")
    sys.exit(1)

MODE = sys.argv[1]
if MODE != "single" and MODE != "closedloop":
    print(f"Usage: {sys.argv[0]} <single|closedloop> <ALG_TYPE>")
    sys.exit(1)
    
ALG_TYPE = sys.argv[2]
OUTPUT_FILE = f"measurements_{MODE}_{ALG_TYPE}_{DEF_CIPH}.txt"

TS_REGEX = re.compile(r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
IMSI_REGEX = re.compile(r"\[(\d{15})\|nas\]")

def parse_ts_ms(line: str):
    m = TS_REGEX.search(line)
    if not m:
        return None
    y, mo, d = map(int, m.group(1,2,3))
    h, mi, s, ms = map(int, m.group(4,5,6,7))
    dt = datetime(y, mo, d, h, mi, s, ms * 1000)
    return int(dt.timestamp() * 1000)

def extract_imsi(line: str):
    m = IMSI_REGEX.search(line)
    return m.group(1) if m else None

def get_starting_imsi_from_cmd(cmd_base):
    for i, arg in enumerate(cmd_base):
        if arg == "-i" and i + 1 < len(cmd_base):
            imsi_str = cmd_base[i + 1]
            return int(imsi_str.replace("imsi-", ""))
    raise ValueError("Parametro -i non trovato in CMD_BASE")

INITIAL_IMSI = get_starting_imsi_from_cmd(CMD_BASE)
results = []

def run_batch(iteration):
    imsi_start = INITIAL_IMSI + (iteration - 1) * N_UE
    imsi_str = f"imsi-{imsi_start:015d}"
    cmd = CMD_BASE.copy()
    cmd[CMD_BASE.index("-i") + 1] = imsi_str
    if N_UE > 1:
        cmd += ["-n", str(N_UE)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        close_fds=True,
    )

    start_times, end_times, deltas = {}, {}, {}
    deadline = time.time() + TIMEOUT

    try:
        for line in proc.stdout:
            ts = parse_ts_ms(line)
            imsi = extract_imsi(line)
            if not ts or not imsi:
                continue

            if "Sending Initial Registration" in line:
                start_times[imsi] = ts
            elif "Initial Registration is successful" in line:
                end_times[imsi] = ts
                if imsi in start_times:
                    deltas[imsi] = end_times[imsi] - start_times[imsi]
                if len(deltas) >= N_UE:
                    break

            if time.time() > deadline:
                break
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    return deltas

def run_once(iteration):
    imsi_start = INITIAL_IMSI + (iteration - 1)
    imsi_str = f"imsi-{imsi_start:015d}"
    cmd = CMD_BASE.copy()
    cmd[CMD_BASE.index("-i") + 1] = imsi_str
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        close_fds=True,
    )

    start_ms = None
    end_ms = None
    deadline = time.time() + TIMEOUT
    fd = proc.stdout
    log_lines = []
    delta = {}

    try:
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                break
            line = fd.readline()
            if line == "":
                if proc.poll() is not None:
                    break
                continue
            log_lines.append(line)
            if "Sending Initial Registration" in line: # <--- starting to count time here
                start_ms = parse_ts_ms(line)
            elif "Initial Registration is successful" in line: # <--- finishing here
                end_ms = parse_ts_ms(line)
                break
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return

    if start_ms is not None and end_ms is not None and end_ms >= start_ms:
        delta[str(imsi_start)] = end_ms - start_ms
        return delta
    else:
        fname = f"measurement_error_{iteration}.txt"
        try:
            with open(fname, "w", encoding="utf-8") as f:
                f.writelines(log_lines)
        except OSError as e:
            print(f"[!] unable to write {fname}: {e}")
        return None


def build_summary(all_results):
    flat = []
    for batch in all_results:
        if isinstance(batch, dict):
            flat.extend(batch.values())
        elif batch is None:
            flat.append(None)

    lines = ["-----------------"]

    valid_values = [v for v in flat if isinstance(v, (int, float)) and v is not None]

    if valid_values:
        flat_sorted = sorted(valid_values)
        n = len(flat_sorted)

        def percentile(p):
            k = (n - 1) * (p / 100)
            f = int(k)
            c = min(f + 1, n - 1)
            return flat_sorted[f] + (flat_sorted[c] - flat_sorted[f]) * (k - f)

        lines.append(f"total UEs measured:  {n}")
        lines.append(f"min:                 {flat_sorted[0]} ms")
        lines.append(f"max:                 {flat_sorted[-1]} ms")
        lines.append(f"avg:                 {statistics.mean(flat_sorted):.2f} ms")
        lines.append(f"median:              {statistics.median(flat_sorted):.2f} ms")
        lines.append(f"95th percentile:     {percentile(95):.2f} ms")
        lines.append(f"99th percentile:     {percentile(99):.2f} ms")
    else:
        lines.append("no valid numeric results")

    null_count = len(flat) - len(valid_values)
    if null_count > 0:
        lines.append(f"[!] warning: {null_count} UEs could not connect within the timeout")

    lines.append("\nTest parameters:")
    lines.append(f"  UEs per batch : {N_UE}")
    lines.append(f"  Iterations    : {ITERATIONS}")
    lines.append(f"  Timeout (s)   : {TIMEOUT}")
    lines.append(f"  Delay (s)     : {DELAY}")
    lines.append(f"  ALG_TYPE      : {ALG_TYPE}")
    lines.append(f"  DEF_CIPH      : {DEF_CIPH}")

    return "\n".join(lines)


def save_results():
    summary = build_summary(results)
    print("\n" + summary)
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n" + summary + "\n")
            for i, batch in enumerate(results, 1):
                f.write(f"Iteration {i}:\n")
                if not batch:
                    f.write("  null\n")
                    continue
                for imsi in sorted(batch.keys()):
                    val = batch[imsi]
                    if val is None:
                        f.write(f"  {imsi}: null\n")
                    else:
                        f.write(f"  {imsi}: {val} ms\n")
    except OSError as e:
        print(f"[!] unable to write {OUTPUT_FILE}: {e}")


def sigint_handler(sig, frame):
    print("\n> saving results")
    save_results()
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, sigint_handler)
    if N_UE != 1:
        desc = f"testing {N_UE} UEs simultaneously"
    else:
        desc = f"testing 1 UE at a time"
    
    with tqdm(total=ITERATIONS, desc=desc, ncols=80) as pbar:
        for i in range(1, ITERATIONS + 1):
            if N_UE != 1:
                batch_deltas = run_batch(i)
            else:
                batch_deltas = run_once(i)
            results.append(batch_deltas)
            pbar.update(1)
            if i < ITERATIONS:
                time.sleep(DELAY)
    save_results()

if __name__ == "__main__":
    main()
