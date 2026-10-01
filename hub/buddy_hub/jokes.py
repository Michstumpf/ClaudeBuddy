"""Now-and-then jokes in the Buddy's speech bubble (and optionally spoken).

Claude Haiku writes a short, clean joke that can riff on the moment (time
of day, weather, how many sessions are busy); a small local list covers the
no-key / API-failure case. Only session counts and statuses go to Haiku,
never prompts or answers.
"""

import logging
import random
import time

from .summarizer import MODEL, load_key, record_usage

log = logging.getLogger("buddy.jokes")

JOKE_MAX_CHARS = 120  # fits 4 lines of the bubble on the 320x240 screen

SYSTEM = """You are Buddy, a small pixel-art mascot on a developer's desk in Canoas, Brazil, who watches their Claude Code sessions.

Now and then you tell one short joke. Make it an actual joke with a punchline: a pun, a question and answer, or a witty twist that lands at the end, the kind a coworker laughs out loud at. Light and upbeat; nothing dark or heavy (no depression, death, illness, insults or politics).

Vary the topic from joke to joke: programming and bugs, developer life, meetings, coffee, Git, deploys, AI assistants, or occasionally the moment you're given (time of day, weather, busy sessions). Don't lean on the same topic twice in a row.

Write it in Brazilian Portuguese only (no English words), at most 15 words. It is shown in a small speech bubble and read aloud, so plain text only: no quotes, emojis, hashtags or preamble. Never repeat or rephrase a joke from the "already told" list."""


FALLBACK = [
    "Por que o programador foi ao médico? Porque estava com muitos bugs.",
    "Funciona na minha máquina. Então vamos mandar a sua máquina para produção.",
    "Existem 10 tipos de pessoas: as que entendem binário e as que não entendem.",
    "Eu não tenho bugs, tenho funcionalidades não documentadas.",
    "Commit às sexta à tarde é esporte radical.",
    "Café entra, código sai. Às vezes sai só bug.",
    "O chimarrão esfria mais rápido que o build termina.",
    "Meu código não tem comentários: ele fala por si. Só não fala comigo.",
    "Testei em produção. Os usuários são os melhores testadores.",
    "Recursão: veja recursão.",
]


def moment(snapshot: dict, weather: dict | None) -> str:
    """What Haiku may riff on: counts and statuses only, no session content."""
    sessions = [s for s in snapshot.get("sessions", []) if s.get("status") != "offline"]
    working = sum(1 for s in sessions if s.get("status") == "working")
    hour = time.localtime().tm_hour
    period = "madrugada" if hour < 6 else "manhã" if hour < 12 else "tarde" if hour < 18 else "noite"
    parts = [f"agora é {period} ({hour}h)", f"{len(sessions)} sessões abertas, {working} trabalhando"]
    if weather:
        parts.append(f"lá fora {weather['temp']}°C, {weather['text']}")
    return "; ".join(parts)


class JokeTeller:
    def __init__(self, api_key: str | None = None, timeout: float = 8.0):
        self.recent: list[str] = []
        self._client = None
        key = api_key if api_key is not None else load_key()
        if key:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout, max_retries=1)

    def _remember(self, joke: str) -> str:
        self.recent = (self.recent + [joke])[-20:]
        return joke

    def _fallback(self) -> str:
        fresh = [j for j in FALLBACK if j not in self.recent] or FALLBACK
        return self._remember(random.choice(fresh))

    async def tell(self, snapshot: dict, weather: dict | None) -> str:
        if not self._client:
            return self._fallback()
        import anthropic

        told = "\n".join(f"- {j}" for j in self.recent[-10:]) or "(nenhuma ainda)"
        content = f"Momento: {moment(snapshot, weather)}\n\nJá contadas:\n{told}"
        try:
            response = await self._client.messages.create(
                model=MODEL,
                max_tokens=120,  # one short line
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("haiku joke failed (%s); using a local one", exc.__class__.__name__)
            return self._fallback()
        record_usage(response)
        text = " ".join("".join(b.text for b in response.content if b.type == "text").split()).strip('"“”')
        if response.stop_reason != "end_turn" or not text or len(text) > JOKE_MAX_CHARS:
            log.info("haiku joke rejected (stop=%s, %d chars); using a local one", response.stop_reason, len(text))
            return self._fallback()
        return self._remember(text)
