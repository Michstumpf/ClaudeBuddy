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
SETTINGS = {"jokes": True, "joke_voice": True, "joke_interval_min": 45, "weather": True,
            "city": "Porto Alegre", "city_geo": {"name": "Porto Alegre", "lat": -30.03, "lon": -51.23}}


def state(*sessions, pending=(), night=False, event=None, settings=None):
    msg = {"type": "state", "sessions": list(sessions), "pending": list(pending), "devices": 1,
           "night": night, "settings": settings or SETTINGS, "weather": WEATHER}
    if event:
        msg["event"] = event
    return msg


def session(name, status, **extra):
    return {"id": name, "name": name, "host": "dell", "status": status, "last_message": "",
            "tmux_pane": "%1", **extra}


def send(msg):
    return ("send", msg)


def tap(x, y):
    return ("tap", x, y)


def battery(percent, charging=False):
    return ("battery", percent, charging)


def expect(text):
    """Wait until the firmware prints this on serial (e.g. what it sends to the hub)."""
    return ("expect", text)


IDLE_GH = {"github": {"reviews": 4, "failing": 1}}
IDLE = state(session("DataHub Sharing Chat", "idle", last_message="Feito: os 38 testes passaram e o pull request está pronto para revisão."),
             session("HIPAA Compliance", "working"), session("git-d6", "offline"))
APPROVAL = {"id": "p1", "session_name": "DataHub Sharing Chat", "tool_name": "Bash",
            "summary": "git push --force origin main", "dangerous": True, "expires_in": 20}

# (screenshot name, actions before it, extra wait in ms for animations)
CASES = [
    ("01-sem-conexao", [], 1500),
    ("02-tranquilo", [send(state(session("DataHub Sharing Chat", "idle"), session("git-d6", "idle")))], 500),
    ("03-trabalhando", [send(state(session("DataHub Sharing Chat", "working"), session("git-d6", "idle")))], 900),
    ("04-aprovacao", [send(state(session("DataHub Sharing Chat", "waiting"), pending=[APPROVAL],
                                 event={"kind": "approval", "session": "DataHub Sharing Chat"}))], 1500),
    ("05-aprovou", [tap(238, 194), expect('tx: {"type":"decision","id":"p1","behavior":"allow","via":"touch"}'),
                    send(state(session("DataHub Sharing Chat", "working")))], 500),
    ("06-terminou", [send(state(session("HIPAA Compliance", "done"),
                                event={"kind": "done", "session": "HIPAA Compliance"}))], 500),
    ("07-piada", [send(state(session("HIPAA Compliance", "idle"))),
                  send({"type": "joke", "text": "Qual é a diferença entre uma reunião e um café? O café eventualmente termina!"})], 600),
    ("08-noite", [send(state(session("HIPAA Compliance", "idle"), night=True))], 500),
    ("09-lista", [send({**IDLE, **IDLE_GH}), tap(160, 100), expect('tx: {"type":"touch"}')], 1500),
    ("10-sessao", [tap(150, 49)], 1500),
    ("11-configuracoes", [tap(48, 193), tap(296, 18)], 1500),
    ("12-config-tocou", [tap(258, 70),
                         expect('tx: {"type":"settings","values":{"jokes":true,"joke_voice":false')], 300),
    ("13-olhos", [tap(48, 196), tap(40, 18), send(state(session("DataHub Sharing Chat", "idle"), settings={**SETTINGS, "skin": "eyes"}))], 1500),
    ("14-olhos-trabalhando", [send(state(session("DataHub Sharing Chat", "working"), settings={**SETTINGS, "skin": "eyes"}))], 900),
    ("15-olhos-esperando", [send(state(session("DataHub Sharing Chat", "waiting"), settings={**SETTINGS, "skin": "eyes"}))], 900),
    ("16-olhos-noite", [send(state(session("DataHub Sharing Chat", "idle"), night=True, settings={**SETTINGS, "skin": "eyes"}))], 900),
    ("17-bateria-carregando", [send(state(session("DataHub Sharing Chat", "idle"))), battery(80, True)], 900),
    ("18-bateria-fraca", [battery(8)], 900),
    ("19-bateria-oculta", [send(state(session("DataHub Sharing Chat", "idle"), settings={**SETTINGS, "battery": False}))], 900),
    ("20-config-rolada", [send(state(session("DataHub Sharing Chat", "idle"))), tap(160, 100), tap(296, 18),
                          ("drag", 160, 160, 160, 60)], 1500),
    ("21-pomodoro-aviso", [tap(48, 194), tap(40, 18),
                           send({**state(session("HIPAA Compliance", "working")),
                                 "pomodoro": {"phase": "focus", "ends_in": 1453, "rounds": 0}, "focus": "pomodoro"}),
                           send({"type": "notice", "kind": "long_task", "text": "HIPAA Compliance está trabalhando há 15 minutos."})], 1200),
]


def build_scenario() -> None:
    steps = ['  - wait-serial: "buddy: ready"']
    for name, actions, wait in CASES:
        for i, action in enumerate(actions):
            following = actions[i + 1][0] if i + 1 < len(actions) else None
            if action[0] == "send":
                m = action[1]
                # json.dumps escapes non-ASCII (é...) exactly like the hub does
                steps.append("  - write-serial: |\n      " + json.dumps(m, separators=(",", ":")))
                steps.append(f'  - wait-serial: "rx: {m["type"] if m["type"] in ("joke", "notice") else "state"}"')
            elif action[0] == "drag":
                _, x1, y1, x2, y2 = action
                steps.append("  - write-serial: |\n      " + json.dumps(
                    {"type": "_drag", "x1": x1, "y1": y1, "x2": x2, "y2": y2}, separators=(",", ":")))
                steps.append('  - wait-serial: "rx: drag"')
                steps.append("  - delay: 800ms")
            elif action[0] == "battery":
                steps.append("  - write-serial: |\n      " + json.dumps(
                    {"type": "_battery", "percent": action[1], "charging": action[2]}, separators=(",", ":")))
                steps.append(f'  - wait-serial: "rx: battery {action[1]}"')
            elif action[0] == "tap":
                steps.append("  - write-serial: |\n      " + json.dumps({"type": "_tap", "x": action[1], "y": action[2]}, separators=(",", ":")))
                steps.append(f'  - wait-serial: "rx: tap {action[1]},{action[2]}"')
                # What a tap sends comes out right away: wait for it with no
                # delay in between, or the line is printed before anyone listens.
                # Two taps in a row need a pause, or the second overrides the first.
                # (and the firmware ignores taps for 400 ms after a screen change).
                if following != "expect":
                    steps.append("  - delay: 700ms")
            else:
                steps.append("  - wait-serial: " + json.dumps(action[1]))
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
    run = subprocess.run([WOKWI, str(ROOT), "--timeout", "300000", "--scenario", str(SCENARIO)],
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
