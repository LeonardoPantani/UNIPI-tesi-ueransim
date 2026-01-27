#!/usr/bin/env python3
import subprocess
import re
import threading
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
N_UE = 1
# stop editing

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NR_UE_BIN = os.path.join(SCRIPT_DIR, "../build", "nr-ue")
NR_CLI_BIN = os.path.join(SCRIPT_DIR, "../build", "nr-cli")
UE_CONFIG = os.path.join(SCRIPT_DIR, "../config", "ue-1.yaml")
TS_REGEX = re.compile(r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
SCRIPT_START_TIME = time.time()
SCRIPT_START_DT = datetime.now()

results = []
active_pgroups = set()
active_pgroups_lock = threading.Lock()


def _parse_ts_ms(line: str):
    m = TS_REGEX.search(line)
    if not m:
        return None
    y, mo, d = map(int, m.group(1, 2, 3))
    h, mi, s, ms = map(int, m.group(4, 5, 6, 7))
    dt = datetime(y, mo, d, h, mi, s, ms * 1000)
    return int(dt.timestamp() * 1000)


def _run_single_imsi(imsi, timeout, error_suffix):
    proc = subprocess.Popen(
        ["sudo", "-n", NR_UE_BIN, "-i", imsi, "-c", UE_CONFIG],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        close_fds=True,
    )

    pgid = os.getpgid(proc.pid)
    with active_pgroups_lock:
        active_pgroups.add(pgid)

    assert proc.stdout is not None
    stdout = proc.stdout

    start_ms = None
    end_ms = None
    deadline = None
    log_lines = []

    try:
        while True:
            if deadline is not None and time.time() > deadline:
                break

            r, _, _ = select.select([stdout], [], [], 0.2)

            if r:
                line = stdout.readline()
                if line:
                    log_lines.append(line)

                    if "Sending Initial Registration" in line:
                        start_ms = _parse_ts_ms(line)
                        if start_ms is not None:
                            deadline = time.time() + timeout

                    elif "Initial Registration is successful" in line:
                        end_ms = _parse_ts_ms(line)
                        break

            if proc.poll() is not None:
                for line in stdout:
                    log_lines.append(line)
                    if "Initial Registration is successful" in line:
                        end_ms = _parse_ts_ms(line)
                break

        if start_ms is not None and end_ms is not None:
            latency = end_ms - start_ms

            time.sleep(0.75)
            try:
                subprocess.run(
                    [NR_CLI_BIN, imsi, "--exec", "deregister switch-off"],
                    stdout=subprocess.DEVNULL,
                    stderr=sys.stderr,
                    timeout=1,
                )
            except subprocess.TimeoutExpired:
                pass

            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            finally:
                with active_pgroups_lock:
                    active_pgroups.discard(pgid)
            return latency

        with open(f"regtimes_error_{error_suffix}.txt", "w", encoding="utf-8") as f:
            f.writelines(log_lines)

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        finally:
            with active_pgroups_lock:
                active_pgroups.discard(pgid)
        return None

    except Exception:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        finally:
            with active_pgroups_lock:
                active_pgroups.discard(pgid)
        with open(f"regtimes_error_{error_suffix}.txt", "w", encoding="utf-8") as f:
            f.writelines(log_lines)
        return None


def _run_once(iteration, timeout):
    imsi = f"imsi-{1010000000001 + iteration - 1:015d}"
    latency = _run_single_imsi(imsi, timeout, f"{iteration}")

    if latency is None:
        return None

    return {imsi: latency}


def _run_batch(iteration, n_ue, timeout):
    base_imsi = 1010000000001 + (iteration - 1) * n_ue
    results = {}
    threads = {}

    sync_barrier = threading.Barrier(n_ue)

    def worker(imsi, key, barrier):
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        results[key] = _run_single_imsi(imsi, timeout, f"iter{iteration}_{imsi}")

    for i in range(n_ue):
        imsi = f"imsi-{base_imsi + i:015d}"
        t = threading.Thread(target=worker, args=(imsi, imsi, sync_barrier))
        threads[imsi] = t
        t.start()

    for t in threads.values():
        t.join()

    return results


def _build_summary(
    all_results, iterations, timeout, delay, alg, sig, batch_size, start_dt, end_dt
):
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

    lines.append(f"\nexperiment start: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"experiment end:   {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")

    lines.append("\nTest parameters:")
    lines.append(f"  UEs per batch : {batch_size}")
    lines.append(f"  Iterations    : {iterations}")
    lines.append(f"  Timeout (s)   : {timeout}")
    lines.append(f"  Delay (s)     : {delay}")
    lines.append(f"  ALG_TYPE      : {alg}")
    lines.append(f"  SIG_TYPE      : {sig}")

    return "\n".join(lines)


def _save_results(
    output_file, iterations, timeout, delay, alg, sig, batch_size, no_results
):
    end_dt = datetime.now()
    summary = _build_summary(
        results,
        iterations,
        timeout,
        delay,
        alg,
        sig,
        batch_size,
        SCRIPT_START_DT,
        end_dt,
    )
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
        "--batch",
        nargs="?",
        const=20,
        type=int,
        help="enable batch mode with optional number of UEs (default: 20)",
    )
    parser.add_argument(
        "--iterations",
        nargs="?",
        const=100,
        type=int,
        help="number of iterations (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="seconds before considering a registration failed (default: 15)",
    )
    parser.add_argument(
        "--delay",
        type=int,
        help="seconds between one iteration and another (default: 1)",
    )
    parser.add_argument(
        "--no-results", action="store_true", help="do not write the output results file"
    )

    args = parser.parse_args()

    mode = args.MODE
    alg = args.ALG_TYPE
    sig = args.SIG_TYPE
    no_results = args.no_results
    batch_size = args.batch if args.batch is not None else 1
    use_batch = args.batch is not None
    iterations = args.iterations if args.iterations is not None else ITERATIONS
    timeout = args.timeout if args.timeout is not None else TIMEOUT
    delay = args.delay if args.delay is not None else DELAY

    output_file = f"regtimes_{mode}_{alg}_{sig}.txt"

    def handler(sig_, frame):
        with active_pgroups_lock:
            for pgid in list(active_pgroups):
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            active_pgroups.clear()

        print("> saving results")
        _save_results(
            output_file, iterations, timeout, delay, alg, sig, batch_size, no_results
        )

        os._exit(0)

    signal.signal(signal.SIGINT, handler)

    with tqdm(
        total=iterations,
        desc=f"testing {batch_size} UE(s) per iteration",
    ) as pbar:
        for i in range(1, iterations + 1):
            if use_batch:
                batch_deltas = _run_batch(i, batch_size, timeout)
            else:
                batch_deltas = _run_once(i, timeout)

            results.append(batch_deltas)
            pbar.update(1)

            if i < iterations:
                time.sleep(delay)

    _save_results(
        output_file, iterations, timeout, delay, alg, sig, batch_size, no_results
    )


if __name__ == "__main__":
    main()
