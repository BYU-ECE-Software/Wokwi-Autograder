#!/usr/bin/env python3
"""
Wokwi autograder runner.

What it does:
  1) Connects to Wokwi, uploads diagram + firmware, and starts a simulation
  2) Drives inputs (e.g., pushbutton) according to a schedule
  3) Samples specified pins ("probes") at important/random timestamps
  4) Captures serial output concurrently
  5) Writes logs (CSV for pin samples, TXT for serial)
  6) Compares logs to golden copies and prints a concise verdict + unified diff

Requires:
  - pip install wokwi-client
  - WOKWI_CLI_TOKEN environment variable (https://wokwi.com/dashboard/ci)
"""
import asyncio
import csv
import difflib
import os
import pathlib
import random
import sys
from typing import Iterable, List, Tuple, Optional

from wokwi_client import WokwiClient, GET_TOKEN_URL  # API docs: https://wokwi.github.io/wokwi-python-client/
from wokwi_client.serial import monitor_lines

# -----------------------
# Config (adjust as needed)
# -----------------------

# Where to find expected/golden logs
EXPECTED_SERIAL_PATH = pathlib.Path("tests/expected_serial.txt")
EXPECTED_PROBES_CSV  = pathlib.Path("tests/expected_probes.csv")  # optional; comment out if you don't have a golden probes file

# Autograder knobs
TIMEOUT_S = 6.0
SERIAL_DONE_TOKEN = "DONE"     # when seen on serial, we consider the test complete
RANDOM_SEED = 1337             # set to None for non-deterministic randomization

# Probes to sample (part, pin, label). Label appears as CSV column header.
# For ESP32 common pins, see your firmware & diagram.json.
PROBES: List[Tuple[str, str, str]] = [
    ("esp", "D26", "LED"),     # example: an LED pin on the MCU
    ("esp", "D4",  "BTN"),     # example: button input (GPIO)
    ("esp", "D5",  "D5"),      # example: extra pin to watch
]

# Times (in seconds) to sample probes. You can mix fixed “important” stamps + randomized stamps.
IMPORTANT_TIMES = [0.48, 0.70, 0.90, 1.10]  # small pre/post around button actions
NUM_RANDOM_TIMES = 6                         # additional random samples in [t_min, t_max]
RAND_WINDOW = (0.2, 1.6)

# Input drive plan for a pushbutton named "btn1" in diagram.json
BUTTON_PRESSES = [
    (0.50, 0.70),  # press at 0.50s, release at 0.70s
    (0.90, 1.10),  # press at 0.90s, release at 1.10s
]

# Filenames for artifacts from this run
ARTIFACT_DIR = pathlib.Path("artifacts")  # CI-friendly
SERIAL_LOG   = ARTIFACT_DIR / "serial.txt"
PROBES_LOG   = ARTIFACT_DIR / "probes.csv"


# -----------------------
# Utility helpers
# -----------------------

def read_expected_lines(path: pathlib.Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text().splitlines()

def unified_diff_str(expected: Iterable[str], actual: Iterable[str], *, fromfile="expected", tofile="actual") -> str:
    return "".join(difflib.unified_diff(list(expected), list(actual), fromfile=fromfile, tofile=tofile, lineterm=""))

def find_firmware(build_dir: pathlib.Path = pathlib.Path("build")) -> pathlib.Path:
    """
    Pick the first *.bin in build directory (ESP-IDF projects).
    Adjust if your toolchain outputs elsewhere.
    """
    try:
        return next(build_dir.glob("*.bin"))
    except StopIteration:
        print("Firmware not found in build/. Did you build your firmware?", file=sys.stderr)
        sys.exit(2)

def ensure_artifacts_dir():
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def make_probe_schedule() -> List[float]:
    """
    Merge important timestamps with randomized samples, sort & unique.
    Random samples are deterministic if RANDOM_SEED is set.
    """
    if RANDOM_SEED is not None:
        random.seed(RANDOM_SEED)
    t_min, t_max = RAND_WINDOW
    rand_times = [random.uniform(t_min, t_max) for _ in range(NUM_RANDOM_TIMES)]
    times = sorted(set(IMPORTANT_TIMES + rand_times))
    return times


# -----------------------
# Orchestration coroutines
# -----------------------

async def capture_serial(transport, done_event: asyncio.Event):
    """
    Stream serial lines to artifacts/serial.txt and detect completion token.
    Uses wokwi_client.serial.monitor_lines (async generator).  Docs show how to
    monitor serial and control simulations programmatically. :contentReference[oaicite:2]{index=2}
    """
    ensure_artifacts_dir()
    with SERIAL_LOG.open("w", encoding="utf-8", newline="") as f:
        async for raw in monitor_lines(transport):
            line = raw.decode(errors="replace").rstrip("\r\n")
            print(line)     # echo to CI logs
            f.write(line + "\n")
            if line.strip().upper() == SERIAL_DONE_TOKEN:
                done_event.set()
                break


async def drive_pushbutton(client: WokwiClient, schedule: List[Tuple[float, float]]):
    """
    Drive a Wokwi pushbutton peripheral (part id 'btn1') using control updates.
    Use small guard bands around the requested time with wait_until_simulation_time.
    - set_control: toggle the 'pressed' control of the button. :contentReference[oaicite:3]{index=3}
    """
    for t_press, t_release in schedule:
        # tiny pre-roll before the press
        await client.wait_until_simulation_time(max(0.0, t_press - 0.002))
        try:
            await client.set_control(part="btn1", control="pressed", value=1)
        except Exception as e:
            print(f"[autograde] set_control press failed @ {t_press:.3f}s: {e!r}", file=sys.stderr)
        # tiny post-roll
        await client.wait_until_simulation_time(t_press + 0.002)

        # hold until release time, then release
        await client.wait_until_simulation_time(t_release + 0.002)
        try:
            await client.set_control(part="btn1", control="pressed", value=0)
        except Exception as e:
            print(f"[autograde] set_control release failed @ {t_release:.3f}s: {e!r}", file=sys.stderr)


async def sample_probes(client: WokwiClient, probes: List[Tuple[str, str, str]], times: List[float]):
    """
    Sample defined probes at precise simulation timestamps and write artifacts/probes.csv
    Columns: time_s, <label1>, <label2>, ...
    - read_pin: reads the digital level; for analog, Wokwi docs describe peripherals that expose other controls. :contentReference[oaicite:4]{index=4}
    """
    ensure_artifacts_dir()
    header = ["time_s"] + [label for *_ignore, label in probes]
    with PROBES_LOG.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)

        for t in times:
            await client.wait_until_simulation_time(t)
            row = [f"{t:.6f}"]
            for part, pin, _label in probes:
                try:
                    val = await client.read_pin(part=part, pin=pin)  # 0/1 for digital
                except Exception as e:
                    print(f"[autograde] read_pin({part},{pin}) failed at {t:.3f}s: {e!r}", file=sys.stderr)
                    val = "ERR"
                row.append(val)
            writer.writerow(row)


async def run_simulation_and_grade():
    token = os.getenv("WOKWI_CLI_TOKEN")
    if not token:
        raise SystemExit(f"Set WOKWI_CLI_TOKEN (get one from {GET_TOKEN_URL}).")

    firmware_bin = find_firmware()
    firmware_elf = firmware_bin.with_suffix(".elf") if firmware_bin.with_suffix(".elf").exists() else None

    client = WokwiClient(token)
    await client.connect()  # connect to Wokwi simulator server (async API) :contentReference[oaicite:5]{index=5}

    # Upload diagram + firmware blobs. Names must match diagram.json/wokwi.toml expectations. :contentReference[oaicite:6]{index=6}
    await client.upload_file("diagram.json")
    await client.upload_file("firmware.bin", local_path=firmware_bin)
    if firmware_elf:
        await client.upload_file("firmware.elf", local_path=firmware_elf)

    # Kick off serial capture before starting simulation
    done_event = asyncio.Event()
    serial_task = asyncio.create_task(capture_serial(client._transport, done_event))

    # Start simulation
    await client.start_simulation(firmware="firmware.bin", elf="firmware.elf" if firmware_elf else None)

    # Drive inputs + run probe sampler concurrently
    probe_times = make_probe_schedule()
    drive_task = asyncio.create_task(drive_pushbutton(client, BUTTON_PRESSES))
    probe_task = asyncio.create_task(sample_probes(client, PROBES, probe_times))

    # Wait for serial "DONE" or overall timeout (whichever first)
    try:
        await asyncio.wait_for(done_event.wait(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        print("[autograde] Timeout waiting for DONE", file=sys.stderr)

    # (Optional) pause/stop – the API supports pause/resume/stop if you want shorter VCDs in UI runs. :contentReference[oaicite:7]{index=7}
    # try:
    #     await client.pause_simulation()
    # except Exception as e:
    #     print(f"[autograde] pause_simulation failed: {e!r}", file=sys.stderr)

    # Ensure our tasks complete
    await asyncio.gather(drive_task, probe_task, return_exceptions=True)

    # Wrap up
    try:
        await client.disconnect()
    finally:
        # Make sure serial task cannot hang forever
        serial_task.cancel()
        with contextlib.suppress(Exception):
            await serial_task

    # -----------------------
    # Grading (serial diff)
    # -----------------------
    actual_serial = read_expected_lines(SERIAL_LOG)
    expected_serial = read_expected_lines(EXPECTED_SERIAL_PATH)

    serial_pass = (expected_serial == actual_serial) if expected_serial else True
    if expected_serial:
        diff = unified_diff_str(expected_serial, actual_serial, fromfile=str(EXPECTED_SERIAL_PATH), tofile=str(SERIAL_LOG))
        if diff:
            print("\n[DIFF] Serial output mismatch:\n" + diff)
        else:
            print("\n[OK] Serial output matches golden copy.")

    # -----------------------
    # Grading (probes diff) – optional
    # -----------------------
    probes_pass = True
    if EXPECTED_PROBES_CSV.exists():
        expected_csv_lines = read_expected_lines(EXPECTED_PROBES_CSV)
        actual_csv_lines = read_expected_lines(PROBES_LOG)
        probes_pass = (expected_csv_lines == actual_csv_lines)
        if not probes_pass:
            diff = unified_diff_str(expected_csv_lines, actual_csv_lines,
                                    fromfile=str(EXPECTED_PROBES_CSV),
                                    tofile=str(PROBES_LOG))
            print("\n[DIFF] Probes CSV mismatch:\n" + diff)
        else:
            print("\n[OK] Probes CSV matches golden copy.")

    # Overall verdict
    if serial_pass and probes_pass:
        print("\n=== GRADE: PASS ===")
        raise SystemExit(0)
    else:
        print("\n=== GRADE: FAIL ===")
        raise SystemExit(1)


# ---------------
# Entrypoint
# ---------------
import contextlib
def main():
    asyncio.run(run_simulation_and_grade())

if __name__ == "__main__":
    main()
