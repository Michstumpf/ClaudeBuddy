#!/usr/bin/env python3
"""Runs every Buddy screen state in Wokwi and saves one screenshot per state.

One simulation (to spare Wokwi CI minutes): the scenario feeds the firmware the
same JSON messages the hub sends, over serial, and screenshots the display
after each. Output: wokwi/screenshots/<state>.png plus contact-sheet.png.

    WOKWI_CLI_TOKEN=... python3 tools/screens.py   (after: pio run -e wokwi_s3)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "wokwi" / "scenarios" / "screens.yaml"
SHOTS = ROOT / "wokwi" / "screenshots"
WOKWI = os.environ.get("WOKWI_CLI", str(Path.home() / ".wokwi" / "bin" / "wokwi-cli"))

WEATHER = {"place": "Canoas", "temp": 17, "text": "nublado", "icon": "☁"}
SETTINGS = {"jokes": True, "joke_voice": True, "joke_interval_min": 45, "weather": True}


def state(*sessions, pending=(), night=False, event=None):
    msg = {"type": "state", "sessions": list(sessions), "pending": list(pending), "devices": 1,
           "night": night, "settings": SETTINGS, "weather": WEATHER}
    if event:
        msg["event"] = event
    return msg


def session(name, status, **extra):
    return {"id": name, "name": name, "host": "dell", "status": status, "last_message": "",
            "tmux_pane": "%1", **extra}


# (screenshot name, messages to send before it, extra wait in ms for animations)
CASES = [
    ("01-sem-conexao", [], 1500),
    ("02-tranquilo", [state(session("DataHub Sharing Chat", "idle"), session("git-d6", "idle"))], 500),
    ("03-trabalhando", [state(session("DataHub Sharing Chat", "working"), session("git-d6", "idle"))], 900),
    ("04-esperando", [state(session("DataHub Sharing Chat", "waiting"),
                            pending=[{"id": "p1", "session_name": "DataHub Sharing Chat", "tool_name": "Bash",
                                      "summary": "git push --force origin main", "dangerous": True,
                                      "expires_at": 0}],
                            event={"kind": "approval", "session": "DataHub Sharing Chat"})], 500),
    ("05-terminou", [state(session("HIPAA Compliance", "done"),
                           event={"kind": "done", "session": "HIPAA Compliance"})], 500),
    ("06-piada", [state(session("HIPAA Compliance", "idle")),
                  {"type": "joke", "text": "Qual é a diferença entre uma reunião e um café? O café eventualmente termina!"}], 600),
    ("07-noite", [state(session("HIPAA Compliance", "idle"), night=True)], 500),
]


def build_scenario() -> None:
    steps = ['  - wait-serial: "buddy: ready"']
    for name, messages, wait in CASES:
        for m in messages:
            # json.dumps escapes non-ASCII (\u00e9...) exactly like the hub does
            steps.append("  - write-serial: |\n      " + json.dumps(m))
            steps.append(f'  - wait-serial: "rx: {"joke" if m["type"] == "joke" else "state"}"')
        steps.append(f"  - delay: {wait}ms")
        steps.append(f"  - take-screenshot:\n      part-id: lcd\n      save-to: ../screenshots/{name}.png")
    SCENARIO.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO.write_text("name: screens\nversion: 1\nauthor: Claude Buddy\nsteps:\n" + "\n".join(steps) + "\n",
                        encoding="utf-8")


def main() -> int:
    if not os.environ.get("WOKWI_CLI_TOKEN"):
        sys.exit("Set WOKWI_CLI_TOKEN (a Wokwi CI token).")
    build_scenario()
    SHOTS.mkdir(parents=True, exist_ok=True)
    run = subprocess.run([WOKWI, str(ROOT), "--timeout", "60000", "--scenario", str(SCENARIO)],
                         capture_output=True, text=True)
    print("\n".join(l for l in run.stdout.splitlines() if l.startswith("[screens]") or "rx:" in l)[-2000:])
    if run.returncode != 0:
        print(run.stdout[-1500:], run.stderr[-1500:])
        return run.returncode
    shots = [str(SHOTS / f"{name}.png") for name, _, _ in CASES]
    labeled = []
    for path in shots:
        out = path.replace(".png", ".label.png")
        subprocess.run(["convert", path, "-background", "#444", "-fill", "white", "-gravity", "south",
                        "-splice", "0x16", "-annotate", "+0+2", Path(path).stem, out], check=True)
        labeled.append(out)
    subprocess.run(["montage", *labeled, "-tile", "4x", "-geometry", "+4+4", "-background", "#666",
                    str(SHOTS / "contact-sheet.png")], check=True)
    for path in labeled:
        os.remove(path)
    print(f"saved {len(shots)} screenshots and {SHOTS / 'contact-sheet.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
