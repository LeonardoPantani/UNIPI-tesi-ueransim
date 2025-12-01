#!/usr/bin/env python3
import subprocess
import re
import time
import statistics
import signal
import select
import os
import sys
import argparse
from datetime import datetime
from tqdm import tqdm

# edit this
ITERATIONS = 100
TIMEOUT = 15
DELAY = 1
# stop editing

TS_REGEX = re.compile(r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
SCRIPT_START_TIME = time.time()

results = []


def _parse_ts_ms(line: str):
    m = TS_REGEX.search(line)
    if not m:
        return None
    y, mo, d = map(int, m.group(1, 2, 3))
    h, mi, s, ms = map(int, m.group(4, 5, 6, 7))
    dt = datetime(y, mo, d, h, mi, s, ms * 1000)
    return int(dt.timestamp() * 1000)


def _run_once(iteration, timeout):
    imsi = f"imsi-{1010000000001 + iteration - 1:015d}"

    proc = subprocess.Popen(
        ["sudo", "-n", "./build/nr-ue", "-i", imsi, "-c", "config/ue-1.yaml"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        close_fds=True,
    )

    start_ms = end_ms = None
    log_lines = []
    deadline = time.time() + timeout

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
                if start_ms is None or end_ms is None:
                    continue

                latency = end_ms - start_ms
                time.sleep(0.75)

                for attempt in range(1, 4):
                    try:
                        subprocess.run(
                            ["./build/nr-cli", imsi, "--exec", "deregister switch-off"],
                            stdout=subprocess.DEVNULL,
                            stderr=sys.stderr,
                            timeout=1,
                        )
                        break
                    except subprocess.TimeoutExpired:
                        if attempt >= 3:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

                return {imsi: latency}

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


def _build_summary(all_results, iterations, timeout, delay, alg, sig):
    flat = []
    for batch in all_results:
        if isinstance(batch, dict):
            flat.extend(batch.values())
        else:
            flat.append(None)

    lines = ["-" * 25]

    valid = [v for v in flat if isinstance(v, (int, float))]
    minutes, seconds = divmod(int(time.time() - SCRIPT_START_TIME), 60)

    if valid:
        valid.sort()
        n = len(valid)

        def percentile(p):
            k = (n - 1) * (p / 100)
            f = int(k)
            c = min(f + 1, n - 1)
            return valid[f] + (valid[c] - valid[f]) * (k - f)

        lines.append(f"total UEs measured:  {n}")
        lines.append(f"elapsed time:        {minutes} min(s) {seconds} sec(s)")
        lines.append(f"min:                 {valid[0]} ms")
        lines.append(f"max:                 {valid[-1]} ms")
        lines.append(f"avg:                 {statistics.mean(valid):.2f} ms")
        lines.append(f"median:              {statistics.median(valid):.2f} ms")
        lines.append(f"95th percentile:     {percentile(95):.2f} ms")
        lines.append(f"99th percentile:     {percentile(99):.2f} ms")
    else:
        lines.append("no valid numeric results")

    null_count = len(flat) - len(valid)
    if null_count > 0:
        lines.append(
            f"[!] warning: {null_count} UEs could not connect within the timeout"
        )

    lines.append("\nTest parameters:")
    lines.append(f"  UEs per batch : 1")
    lines.append(f"  Iterations    : {iterations}")
    lines.append(f"  Timeout (s)   : {timeout}")
    lines.append(f"  Delay (s)     : {delay}")
    lines.append(f"  ALG_TYPE      : {alg}")
    lines.append(f"  SIG_TYPE      : {sig}")

    return "\n".join(lines)


def _save_results(output_file, iterations, timeout, delay, alg, sig, no_results):
    summary = _build_summary(results, iterations, timeout, delay, alg, sig)
    print("\n" + summary)

    if no_results:
        return

    try:
        with open(output_file, "w", encoding="utf-8") as f:
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
        print(f"[!] unable to write {output_file}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Runs repeated UE registrations and measures latency."
    )

    parser.add_argument("MODE", choices=["nosr", "sr"])
    parser.add_argument("ALG_TYPE", help="Algorithm Type")
    parser.add_argument("SIG_TYPE", help="Signature Type")

    parser.add_argument(
        "--no-results", action="store_true", help="do not write the output results file"
    )

    args = parser.parse_args()

    mode = args.MODE
    alg = args.ALG_TYPE
    sig = args.SIG_TYPE
    no_results = args.no_results

    output_file = f"regtimes_{mode}_{alg}_{sig}.txt"

    def handler(sig_, frame):
        print("\n> saving results")
        _save_results(output_file, ITERATIONS, TIMEOUT, DELAY, alg, sig, no_results)
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)

    with tqdm(total=ITERATIONS, desc="testing 1 UE at a time") as pbar:
        for i in range(1, ITERATIONS + 1):
            batch_deltas = _run_once(i, TIMEOUT)
            results.append(batch_deltas)
            pbar.update(1)
            if i < ITERATIONS:
                time.sleep(DELAY)

    _save_results(output_file, ITERATIONS, TIMEOUT, DELAY, alg, sig, no_results)


if __name__ == "__main__":
    main()
