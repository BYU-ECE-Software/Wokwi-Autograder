#!/usr/bin/env python3
"""
Wokwi autograder (probes-only)

What it does:
  1) Connects to Wokwi, uploads diagram + firmware, and starts a simulation
  2) Drives inputs (e.g., pushbutton) via set_control()
  3) Samples defined probes at specific timestamps using read_pin()
  4) Writes artifacts/probes.csv
  5) Compares artifacts/probes.csv to tests/expected_probes.csv (golden)
  6) Prints PASS/FAIL and a unified diff on mismatch

Notes:
  - Deterministic by default: fixed RANDOM_SEED plus fixed IMPORTANT_TIMES.
  - No serial I/O is used for grading or completion.
  - If you change sampling times or seed, regenerate the golden CSV.
"""

import asyncio
import csv
import difflib
import os
import pathlib
import random
import sys
from typing import Iterable, List, Tuple

from wokwi_client import WokwiClient, GET_TOKEN_URL  # https://wokwi.github.io/wokwi-python-client/

# -----------------------
# Config
# -----------------------

EXPECTED_PROBES_CSV  = pathlib.Path("tests/expected_probes.csv")

# Simulation timing
RANDOM_SEED = 1337                  # keep fixed for deterministic random samples
IMPORTANT_TIMES = [0.48, 0.70, 0.90, 1.10]
NUM_RANDOM_TIMES = 6                # additional random samples in [t_min, t_max]
RAND_WINDOW = (0.20, 1.60)

# Input drive plan for a pushbutton named "btn1" in diagram.json
BUTTON_PRESSES = [
    (0.50, 0.70),                   # press at 0.50s, release at 0.70s
    (0.90, 1.10),                   # press at 0.90s, release at 1.10s
]

# Probes to sample (part, pin, label) -> CSV columns are time_s + labels
PROBES: List[Tuple[str, str, str]] = [
    ("esp", "D26", "LED"),
    ("esp", "D4",  "BTN"),
    ("esp", "D5",  "D5"),
]

# Artifacts
ARTIFACT_DIR = pathlib.Path("tools/artifacts")
PROBES_LOG   = ARTIFACT_DIR / "probes.csv"

# -----------------------
# Helpers
# -----------------------

def ensure_artifacts_dir():
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def find_firmware(build_dir: pathlib.Path = pathlib.Path("build")) -> pathlib.Path:
    """Pick the first *.bin in build/ (ESP-IDF-style)."""
    try:
        return next(build_dir.glob("*.bin"))
    except StopIteration:
        print("Firmware not found in build/. Did you build your firmware?", file=sys.stderr)
        sys.exit(2)

def make_probe_schedule() -> List[float]:
    """Merge fixed 'important' times with seeded-random times and sort."""
    random.seed(RANDOM_SEED)
    t_min, t_max = RAND_WINDOW
    rand_times = [random.uniform(t_min, t_max) for _ in range(NUM_RANDOM_TIMES)]
    return sorted(set(IMPORTANT_TIMES + rand_times))

def read_lines(path: pathlib.Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text().splitlines()

def diff_str(expected: Iterable[str], actual: Iterable[str],
             fromfile="tests/expected_probes.csv", tofile="artifacts/probes.csv") -> str:
    return "".join(difflib.unified_diff(list(expected), list(actual),
                                        fromfile=fromfile, tofile=tofile, lineterm=""))

# -----------------------
# Orchestration
# -----------------------

async def drive_pushbutton(client: WokwiClient, schedule: List[Tuple[float, float]]):
    """
    Toggle 'btn1' pressed control at the requested times.
    Uses wait_until_simulation_time() to align actions with sim time.
    """
    for t_press, t_release in schedule:
        await client.wait_until_simulation_time(max(0.0, t_press - 0.002))
        try:
            await client.set_control(part="btn1", control="pressed", value=1)
        except Exception as e:
            print(f"[autograde] set_control press failed @{t_press:.3f}s: {e!r}", file=sys.stderr)

        await client.wait_until_simulation_time(t_press + 0.002)
        await client.wait_until_simulation_time(t_release + 0.002)

        try:
            await client.set_control(part="btn1", control="pressed", value=0)
        except Exception as e:
            print(f"[autograde] set_control release failed @{t_release:.3f}s: {e!r}", file=sys.stderr)

async def sample_probes(client: WokwiClient,
                        probes: List[Tuple[str, str, str]],
                        times: List[float]):
    """
    Sample probes at specific timestamps and write a CSV:
      time_s, <label1>, <label2>, ...
    """
    ensure_artifacts_dir()
    header = ["time_s"] + [label for _, _, label in probes]
    with PROBES_LOG.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)

        for t in times:
            await client.wait_until_simulation_time(t)
            row = [f"{t:.6f}"]
            for part, pin, _label in probes:
                try:
                    val = await client.read_pin(part=part, pin=pin)  # digital: 0/1
                except Exception as e:
                    print(f"[autograde] read_pin({part},{pin}) failed @{t:.3f}s: {e!r}", file=sys.stderr)
                    val = "ERR"
                row.append(val)
            writer.writerow(row)

async def run_and_grade():
    token = os.getenv("WOKWI_CLI_TOKEN")
    if not token:
        raise SystemExit(f"Set WOKWI_CLI_TOKEN (get one from {GET_TOKEN_URL}).")

    firmware_bin = find_firmware()
    firmware_elf = firmware_bin.with_suffix(".elf")
    if not firmware_elf.exists():
        firmware_elf = None  # elf optional

    client = WokwiClient(token)
    await client.connect()

    # Upload files; filenames should match your diagram.json / wokwi.toml expectations.
    await client.upload_file("diagram.json")
    await client.upload_file("firmware.bin", local_path=firmware_bin)
    if firmware_elf:
        await client.upload_file("firmware.elf", local_path=firmware_elf)

    await client.start_simulation(firmware="firmware.bin", elf="firmware.elf" if firmware_elf else None)

    # Run input driving + probe sampling concurrently.
    times = make_probe_schedule()
    await asyncio.gather(
        drive_pushbutton(client, BUTTON_PRESSES),
        sample_probes(client, PROBES, times),
    )

    # Optional: pause/stop if desired; not required for grading.
    # await client.pause_simulation()

    await client.disconnect()

    # Grade based ONLY on probes.csv
    actual = read_lines(PROBES_LOG)
    expected = read_lines(EXPECTED_PROBES_CSV)
    if not expected:
        print("No golden probes CSV found. To create one, copy artifacts/probes.csv -> tests/expected_probes.csv")
        print("\n=== GRADE: PASS (no golden to compare) ===")
        raise SystemExit(0)

    if expected == actual:
        print("\n[OK] Probes CSV matches golden copy.")
        print("\n=== GRADE: PASS ===")
        raise SystemExit(0)

    print("\n[DIFF] Probes CSV mismatch:")
    print(diff_str(expected, actual))
    print("\n=== GRADE: FAIL ===")
    raise SystemExit(1)

def main():
    asyncio.run(run_and_grade())

if __name__ == "__main__":
    main()
