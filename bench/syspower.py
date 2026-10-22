"""Whole-machine power sampler: GPU and CPU measured, memory, board and PSU losses estimated.

One JSON line per nvidia-smi reading:
{"t": epoch s, "gpu_w", "cpu_w", "dram_w", "rest_w", "wall_w"}. gpu_w comes from nvidia-smi,
cpu_w from the RAPL package counter (on Zen 2 the CPU's own model, not a shunt). dram_w and
rest_w are the constants given (Zen 2 has no DRAM domain and no board sensor reports watts),
and wall_w divides the DC total by the PSU efficiency. Only gpu_w and cpu_w are measured.

RAPL's energy_uj is root-only by default (Platypus). A udev rule can open it to one group:
    SUBSYSTEM=="powercap", ACTION=="add", RUN+="/bin/chgrp wheel /sys%p/energy_uj", \
        RUN+="/bin/chmod g+r /sys%p/energy_uj"

Sample:    python3 bench/syspower.py --out power.jsonl [--interval-ms 1000]
Summarize: python3 bench/syspower.py --summarize power.jsonl [--tariff 1.168788]
"""

import argparse
import json
import signal
import subprocess
import sys
import time
from itertools import pairwise
from pathlib import Path

RAPL = Path("/sys/class/powercap/intel-rapl:0")
# What one more kWh costs on the bill: tariff with ICMS and PIS/COFINS (1.146179) plus the
# bandeira amarela (0.022609), Oct 2026. The flag changes monthly; verde drops it to 1.146179.
# skrode/benchsummary.py still uses the pre-tax 0.89181 (BL-001).
TARIFF_BRL_KWH = 1.168788
PARTS = ("gpu_w", "cpu_w", "dram_w", "rest_w", "wall_w")


def rapl_delta_uj(before: int, after: int, max_range_uj: int) -> int:
    """Energy between two counter reads, across at most one wrap."""
    return after - before if after >= before else after + max_range_uj - before


def wall_watts(dc_watts: float, psu_efficiency: float) -> float:
    return dc_watts / psu_efficiency


def read_uj(path: Path) -> int:
    return int(path.read_text(encoding="ascii"))


def sample(ns: argparse.Namespace) -> int:
    energy, max_range = RAPL / "energy_uj", read_uj(RAPL / "max_energy_range_uj")
    try:
        last_uj, last_t = read_uj(energy), time.monotonic()
    except PermissionError:
        sys.exit(f"{energy} is not readable; see the udev rule in this script's docstring")
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
                gpu = float(line.strip())
            except ValueError:  # "[N/A]" or a partial line at shutdown
                continue
            now_uj, now_t = read_uj(energy), time.monotonic()
            cpu = rapl_delta_uj(last_uj, now_uj, max_range) / 1e6 / (now_t - last_t)
            last_uj, last_t = now_uj, now_t
            dc = gpu + cpu + ns.dram_w + ns.rest_w
            row = {"t": round(time.time(), 3), "gpu_w": gpu, "cpu_w": round(cpu, 2),
                   "dram_w": ns.dram_w, "rest_w": ns.rest_w,
                   "wall_w": round(wall_watts(dc, ns.psu_efficiency), 2)}
            handle.write(json.dumps(row) + "\n")
            handle.flush()
    smi.wait()
    return 0


def summarize(path: Path, tariff: float) -> dict:
    """Mean watts and Wh per part; each row's watts held over the gap since the previous row."""
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    if len(rows) < 2:
        raise ValueError(f"{path}: need at least two samples")
    wh = dict.fromkeys(PARTS, 0.0)
    for prev, row in pairwise(rows):
        hours = (row["t"] - prev["t"]) / 3600
        for part in PARTS:
            wh[part] += row[part] * hours
    seconds = rows[-1]["t"] - rows[0]["t"]
    return {"seconds": round(seconds, 1), "samples": len(rows),
            "mean_w": {p: round(wh[p] * 3600 / seconds, 1) for p in PARTS},
            "peak_w": {p: max(r[p] for r in rows) for p in PARTS},
            "wh": {p: round(wh[p], 3) for p in PARTS},
            "wall_brl": round(wh["wall_w"] / 1000 * tariff, 5)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out")
    mode.add_argument("--summarize", type=Path)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--dram-w", type=float, default=6.0, help="estimate: 2 DDR4 sticks")
    parser.add_argument("--rest-w", type=float, default=30.0,
                        help="estimate: board, chipset, fans, drives")
    parser.add_argument("--psu-efficiency", type=float, default=0.88)
    parser.add_argument("--tariff", type=float, default=TARIFF_BRL_KWH, help="R$ per kWh")
    ns = parser.parse_args()
    if ns.summarize:
        print(json.dumps(summarize(ns.summarize, ns.tariff), indent=2))
        return 0
    return sample(ns)


if __name__ == "__main__":
    sys.exit(main())
