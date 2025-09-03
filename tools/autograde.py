#!/usr/bin/env python3
import asyncio, os, pathlib, difflib, sys
from wokwi_client import WokwiClient, GET_TOKEN_URL
from wokwi_client.serial import monitor_lines

# Remove main.c from student view, figure out a way to import student code into autograder and then Get VCD output from logic analyzer

EXPECTED = pathlib.Path("tests/expected_serial.txt").read_text().splitlines()
TIMEOUT_S = 6.0

# Drive the pushbutton: pressed -> released
async def drive_button(client):

    async def press_at(t_press: float, t_release: float):
        await client.wait_until_simulation_time(t_press - 0.002)  # small pre-roll
        try:
            await client.set_control(part="btn1", control="pressed", value=1)
            await client.wait_until_simulation_time(t_press + 0.002)  # small post-roll
            # p = await client.read_pin(part="esp", pin="D4")
            # print(f"[debug] after press@{t_press:.3f}s D4={p}")
        except Exception as e:
            print(f"[autograde] set_control press failed: {e!r}", file=sys.stderr)

        await client.wait_until_simulation_time(t_release + 0.002)
        try:
            await client.set_control(part="btn1", control="pressed", value=0)
            await client.wait_until_simulation_time(t_release + 0.002)
            # p = await client.read_pin(part="esp", pin="D4")
            # print(f"[debug] after release@{t_release:.3f}s D4={p}")
        except Exception as e:
            print(f"[autograde] set_control release failed: {e!r}", file=sys.stderr)
    
    await press_at(1.1, 1.3)  # initial press
    await press_at(1.7, 2.1)  # second press

async def main():
    token = os.getenv("WOKWI_CLI_TOKEN")
    if not token:
        raise SystemExit(f"Set WOKWI_CLI_TOKEN (get one from {GET_TOKEN_URL}).")

    fw_path = pathlib.Path("build").glob("*.bin")
    try:
        firmware = next(fw_path)
    except StopIteration:
        print("Firmware not found in build/. Did you build with idf.py?", file=sys.stderr)
        sys.exit(2)

    client = WokwiClient(token)
    await client.connect()  # connect to Wokwi simulator server
    # Upload required files
    await client.upload_file("diagram.json")
    await client.upload_file("wokwi_button_led.elf", local_path=firmware)
    await client.upload_file("wokwi_button_led.bin", local_path=firmware)


    # Start the simulation and stream serial output
    captured = []
    done_event = asyncio.Event()
    release_count = 0  # for debugging, not used in the final version

    

    async def capture_serial():
        nonlocal release_count
        async for raw in monitor_lines(client._transport):  # internal helper used by serial_monitor_cat
            line = raw.decode(errors="replace").rstrip("\r\n")
            print(line)  # echo to CI logs
            captured.append(line)
            if line.strip().upper() == "DONE":
                done_event.set()
                break
            # if line.strip() == "EVENT: Button Release":
            #     release_count += 1
            #     if release_count >= 2:
            #         done_event.set()
            #         break


    cap_task = asyncio.create_task(capture_serial())

    await client.start_simulation(firmware="wokwi_button_led.bin", elf="wokwi_button_led.elf")

    sim_task = asyncio.create_task(drive_button(client))

    # Wait for DONE or a generous timeout (we're driving after 1.6s)
    try:
        await asyncio.wait_for(done_event.wait(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        print("[autograde] Timeout waiting for DONE", file=sys.stderr)

    # try:
    #     await client.pause_simulation()
    # except Exception as e:
    #     print(f"[autograde] pause_simulation failed: {e!r}", file=sys.stderr)

    # client.stop_serial_monitors()
    # await client.stop_simulation()
    # await client.pause_simulation()
    # await client.stop_simulation()
    # await client.disconnect()

    print("Stopped Sim!")

    #vcd_path = pathlib.Path("tests") / "Lab1.vcd"
    # Prefer the expected name, but fall back to defaults in case attrs didn’t apply
    for name in ("Lab1.vcd", "wokwi-logic.vcd", "Lab1.vcd.vcd"):
        try:
            await client.download_file(name)#, local_path=vcd_path)
            print(f"[autograde] downloaded {name} -> {"HERE"}") #Formally VCD Path
            break
        except Exception as e:
            last_err = e
    else:
        print(f"[autograde] could not find VCD (tried Lab1.vcd, wokwi-logic.vcd, Lab1.vcd.vcd). Last error: {last_err}", file=sys.stderr)

    # Pull down the VCD produced by the logic analyzer
    # vcd_name = "Lab1.vcd"  # matches attrs.filename + ".vcd"
    # vcd_path = pathlib.Path("tests") / "Lab1.vcd"
    # await client.download_file(vcd_name, local_path=vcd_path)

    # await client.stop_simulation()
    await client.disconnect()

    try:
        from vcdvcd import VCDVCD
        vcd = VCDVCD(str(vcd_path), only_sigs=True)
        # Examples of channel names:
        #   vcd.signals -> list of hierarchical names; Wokwi sets them
        #   If you set channelNames, you’ll find "LED" and "BTN".
        # Quick autograde idea: check that BTN has two rising edges and
        # LED goes high-low-high in response with plausible timing.
        # (Implement your preferred checks here)
    except Exception as e:
        print(f"[autograde] VCD parse warning: {e}", file=sys.stderr)

    # if not sim_task.done():
    #     sim_task.cancel()
    #     try:
    #         await sim_task
    #     except asyncio.CancelledError:
    #         pass
    
    # await client.disconnect()

    # if not cap_task.done():
    #     cap_task.cancel()
    #     try:
    #         await cap_task
    #     except asyncio.CancelledError:
    #         pass

    # try:
    #     await asyncio.wait_for(done_event.wait(), timeout=TIMEOUT_S)
    # except asyncio.TimeoutError:
    #     print("\n[autograde] Timeout waiting for DONE", file=sys.stderr)

    # Cleanup capture task (don’t hang if it’s still running)
    # if not cap_task.done():
    #     cap_task.cancel()
    #     try:
    #         await cap_task
    #     except asyncio.CancelledError:
    #         pass

    # Optional: sanity check MCU LED pin after final release (should be 0)
    # try:
    #     pin = await client.read_pin(part="esp", pin="D26")
    #     # pin["value"] can be inspected/logged if desired
    # except Exception:
    #     pass

    # Compare serial with expected (loose: ensure lines appear in order)
    def subseq(a, b):
        it = iter(b)
        return all(any(x == y for y in it) for x in a)
    ok = subseq(EXPECTED, captured)

    # # # Always write what we saw
    # pathlib.Path("tests").mkdir(exist_ok=True)
    # with open("tests/captured_serial.txt", "w") as f:
    #     f.write("\n".join(captured))

    # if not ok:
    #     print("\n=== DIFF EXPECTED vs ACTUAL ===", file=sys.stderr)
    #     diff = difflib.unified_diff(EXPECTED, captured, fromfile="expected", tofile="actual", lineterm="")
    #     print("\n".join(diff), file=sys.stderr)
    #     await client.disconnect()
    #     sys.exit(1)

    # await client.disconnect()



if __name__ == "__main__":
    asyncio.run(main())
