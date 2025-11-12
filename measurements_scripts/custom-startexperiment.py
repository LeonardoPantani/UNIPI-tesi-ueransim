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
N_UE = 1 # single: 1, closed_loop: 10
ITERATIONS = 100 # single: 100, closed_loop: 100
TIMEOUT = 15 # single: 5, closed_loop: 10
DELAY = 1 # single: 1, closed_loop: 3
# stop editing

# --- check parameters and preparation
if len(sys.argv) < 4:
    print(f"Usage: {sys.argv[0]} <nosr|sr> <ALG_TYPE> <SIG_TYPE>")
    sys.exit(1)

MODE = sys.argv[1]
if MODE != "nosr" and MODE != "sr":
    print(f"Usage: {sys.argv[0]} <nosr|sr> <ALG_TYPE> <SIG_TYPE>")
    sys.exit(1)
    
ALG_TYPE = sys.argv[2]
SIG_TYPE = sys.argv[3]
OUTPUT_FILE = f"regtimes_{MODE}_{ALG_TYPE}_{SIG_TYPE}.txt"

TS_REGEX = re.compile(r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
IMSI_REGEX = re.compile(r"\[(\d{15})\|nas\]")
SCRIPT_START_TIME = time.time()

# ---- defining useful functions
def _parse_ts_ms(line: str):
    m = TS_REGEX.search(line)
    if not m:
        return None
    y, mo, d = map(int, m.group(1,2,3))
    h, mi, s, ms = map(int, m.group(4,5,6,7))
    dt = datetime(y, mo, d, h, mi, s, ms * 1000)
    return int(dt.timestamp() * 1000)

def _get_starting_imsi_from_cmd(cmd_base):
    for i, arg in enumerate(cmd_base):
        if arg == "-i" and i + 1 < len(cmd_base):
            imsi_str = cmd_base[i + 1]
            return int(imsi_str.replace("imsi-", ""))
    raise ValueError("-i parameter not found in CMD_BASE")

INITIAL_IMSI = _get_starting_imsi_from_cmd(CMD_BASE)
results = []

# --- running one UE
def run_once(iteration):
    imsi = f"imsi-{INITIAL_IMSI + iteration - 1:015d}"
    cmd = CMD_BASE.copy()
    cmd[CMD_BASE.index("-i") + 1] = imsi

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, preexec_fn=os.setsid, close_fds=True
    )

    start_ms = end_ms = None
    log_lines = []
    deadline = time.time() + TIMEOUT

    try:
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 0.2)
            if not r:
                continue

            assert proc.stdout is not None
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            log_lines.append(line)

            if "Sending Initial Registration" in line:
                start_ms = _parse_ts_ms(line)

            elif "Initial Registration is successful" in line:
                end_ms = _parse_ts_ms(line)
                if start_ms is not None and end_ms is not None:
                    latency = end_ms - start_ms
                else:
                    continue
                time.sleep(0.75)
                for attempt in range(1, 4):
                    try:
                        subprocess.run(
                            ["./build/nr-cli", imsi, "--exec", "deregister switch-off"],
                            stdout=subprocess.DEVNULL, stderr=sys.stderr, timeout=1
                        )
                        break
                    except subprocess.TimeoutExpired:
                        if attempt >= 3:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                return {imsi: latency}

        #  registration failed or timeout
        print(f"[!] UE {imsi} did not register in time")
        with open(f"regtimes_error_{iteration}.txt", "w", encoding="utf-8") as f:
            f.writelines(log_lines)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return None

    except Exception as e:
        print(f"[!] Error in iteration {iteration}: {e}")
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        with open(f"regtimes_error_{iteration}.txt", "w", encoding="utf-8") as f:
            f.writelines(log_lines)
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
    (minutes, seconds) = elapsed_time()

    if valid_values:
        flat_sorted = sorted(valid_values)
        n = len(flat_sorted)

        def percentile(p):
            k = (n - 1) * (p / 100)
            f = int(k)
            c = min(f + 1, n - 1)
            return flat_sorted[f] + (flat_sorted[c] - flat_sorted[f]) * (k - f)

        lines.append(f"total UEs measured:  {n}")
        lines.append(f"Elapsed time:        {minutes} min(s) {seconds} sec(s)")
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
    lines.append(f"  SIG_TYPE      : {SIG_TYPE}")

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

def elapsed_time():
    return (divmod(int(time.time() - SCRIPT_START_TIME), 60))

def sigint_handler(sig, frame):
    print("\n> saving results")
    save_results()
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, sigint_handler)
    with tqdm(total=ITERATIONS, desc="testing 1 UE at a time") as pbar:
        for i in range(1, ITERATIONS + 1):
            batch_deltas = run_once(i)
            results.append(batch_deltas)
            pbar.update(1)
            if i < ITERATIONS:
                time.sleep(DELAY)
    save_results()


if __name__ == "__main__":
    main()
