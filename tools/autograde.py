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

        led = await client.read_pin(part="esp", pin="D26")  # MCU LED pin, just to see if it works
        button = await client.read_pin(part="esp", pin="D4")
        p = await client.read_pin(part="esp", pin="D5")

        print(f"[debug] at press@{t_press:.3f}s LED={led}, BTN={button}, D5={p}")

        await client.wait_until_simulation_time(t_release + 0.002)
        try:
            await client.set_control(part="btn1", control="pressed", value=0)
            await client.wait_until_simulation_time(t_release + 0.002)
            # p = await client.read_pin(part="esp", pin="D4")
            # print(f"[debug] after release@{t_release:.3f}s D4={p}")
        except Exception as e:
            print(f"[autograde] set_control release failed: {e!r}", file=sys.stderr)
    
    await press_at(.5, .7)  # initial press
    await press_at(.9, 1.1)  # second press

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

   
    await client.disconnect()




if __name__ == "__main__":
    asyncio.run(main())
