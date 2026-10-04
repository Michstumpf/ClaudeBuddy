"""Pomodoro timer on the hub: focus / break cycles, announced on the Buddy.

While a focus period runs the Buddy is in focus mode (no jokes).
"""

import time

FOCUS_MIN, BREAK_MIN = 25, 5


class Pomodoro:
    def __init__(self, focus_min: int = FOCUS_MIN, break_min: int = BREAK_MIN):
        self.focus_s, self.break_s = focus_min * 60, break_min * 60
        self.phase: str | None = None  # "focus" | "break" | None
        self.ends_at = 0.0
        self.rounds = 0

    def start(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        self.phase, self.ends_at, self.rounds = "focus", now + self.focus_s, 0
        return f"Pomodoro: {self.focus_s // 60} minutos de foco. Bora!"

    def stop(self) -> str | None:
        if self.phase is None:
            return None
        self.phase = None
        return "Pomodoro encerrado."

    def tick(self, now: float | None = None) -> str | None:
        """Moves to the next phase when the current one ends; returns its notice."""
        now = time.time() if now is None else now
        if self.phase is None or now < self.ends_at:
            return None
        if self.phase == "focus":
            self.rounds += 1
            self.phase, self.ends_at = "break", now + self.break_s
            return f"Hora da pausa! {self.break_s // 60} minutos para levantar e tomar uma água."
        self.phase, self.ends_at = "focus", now + self.focus_s
        return "Pausa acabou, de volta ao foco!"

    @property
    def focusing(self) -> bool:
        return self.phase == "focus"

    def public(self, now: float | None = None) -> dict | None:
        if self.phase is None:
            return None
        now = time.time() if now is None else now
        return {"phase": self.phase, "ends_in": max(0, round(self.ends_at - now)), "rounds": self.rounds}
