"""GPU power sampler: one JSON line per nvidia-smi reading, {"t": epoch seconds, "w": watts}.

Runs until SIGTERM or SIGINT. GPU only: the CPU's RAPL counter is root-only on this machine,
so CPU and PSU losses are not measured (stated wherever the numbers are used).

Run: python3 bench/sampler.py --out power.jsonl [--interval-ms 500]
"""

import argparse
import json
import signal
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval-ms", type=int, default=500)
    ns = parser.parse_args()
    smi = subprocess.Popen(
        ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits",
         f"-lms={ns.interval_ms}"],
        stdout=subprocess.PIPE, text=True,
    )

    def stop(*_) -> None:
        smi.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with open(ns.out, "a", encoding="utf-8") as handle:
        for line in smi.stdout:
            try:
                watts = float(line.strip())
            except ValueError:  # "[N/A]" or a partial line at shutdown
                continue
            handle.write(json.dumps({"t": round(time.time(), 3), "w": watts}) + "\n")
            handle.flush()
    smi.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
