#!/usr/bin/env python3
"""Connects the simulated Buddy (Wokwi) to the real hub, until the firmware has WiFi.

Wokwi exposes the simulated serial port on localhost:4000 (rfc2217ServerPort in
wokwi.toml). This bridge joins the hub's WebSocket as a Buddy device and:
  hub -> serial: every hub message, one JSON per line (what the firmware's
                 serial link reads)
  serial -> hub: every "tx: {...}" line the firmware prints (taps, decisions,
                 settings)
Other serial lines are shown here as the Buddy's log.

    ../.venv/bin/python tools/hub_bridge.py      (with the simulation running)
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import serial  # pyserial
import websockets

HUB = os.environ.get("BUDDY_HUB", "ws://127.0.0.1:8765").rstrip("/")
PORT = os.environ.get("BUDDY_SIM_SERIAL", "rfc2217://localhost:4000")
TOKEN = os.environ.get("BUDDY_TOKEN") or (Path.home() / ".config" / "claude-buddy" / "token").read_text().strip()


def open_serial() -> serial.SerialBase:
    while True:
        try:
            return serial.serial_for_url(PORT, baudrate=115200, timeout=0.2)
        except (serial.SerialException, OSError):
            print(f"waiting for the simulation's serial port at {PORT}…", flush=True)
            time.sleep(2)


def serial_reader(port, loop: asyncio.AbstractEventLoop, outgoing: asyncio.Queue) -> None:
    buf = b""
    while True:
        try:
            chunk = port.read(4096)
        except (serial.SerialException, OSError):
            print("simulation serial port closed", flush=True)
            loop.call_soon_threadsafe(outgoing.put_nowait, None)
            return
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("tx: {"):
                loop.call_soon_threadsafe(outgoing.put_nowait, line[4:])
                print(f"buddy -> hub {line[4:]}", flush=True)
            elif line:
                print(f"buddy | {line}", flush=True)


async def main() -> None:
    port = open_serial()
    print(f"serial connected ({PORT}); joining the hub at {HUB}", flush=True)
    loop = asyncio.get_running_loop()
    outgoing: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=serial_reader, args=(port, loop, outgoing), daemon=True).start()
    async with websockets.connect(f"{HUB}/ws?token={TOKEN}", max_size=None) as ws:
        async def to_hub():
            while (msg := await outgoing.get()) is not None:
                await ws.send(msg)
            await ws.close()

        async def to_buddy():
            async for msg in ws:
                kind = json.loads(msg).get("type")
                if kind == "speech":
                    print("hub -> buddy speech (the simulation has no speaker)", flush=True)
                port.write(msg.encode("ascii", "backslashreplace") + b"\n")

        await asyncio.gather(to_hub(), to_buddy())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
