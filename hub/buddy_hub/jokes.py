"""Now-and-then jokes in the Buddy's speech bubble (and optionally spoken).

Claude Haiku writes a short, clean joke; a small local list covers the
no-key / API-failure case. The hub picks each joke's topic and format so they
vary: left alone, Haiku kept riffing on the one concrete thing it was given
(how many sessions are open). Now and then a joke is about the moment instead,
with one detail of it (time of day, weather or session counts). Only counts and
statuses go to Haiku, never prompts or answers.

Your own jokes: ~/.config/claude-buddy/jokes.txt, one per line (format in
hub/jokes.example.txt, which also has a prompt to have an AI write them). When
it has jokes, some of the jokes come from it, and it replaces the built-in
list when Haiku is unavailable. Read on every joke, so edits apply at once.

The last jokes and topics are kept in ~/.local/share/claude-buddy/jokes.json,
so a hub restart does not bring the same ones back.
"""

import json
import logging
import random
import re
import time
from pathlib import Path

from .summarizer import MODEL, load_key, record_usage

log = logging.getLogger("buddy.jokes")

JOKE_MAX_CHARS = 120  # fits 4 lines of the bubble on the 320x240 screen

JOKES_FILE = Path.home() / ".local" / "share" / "claude-buddy" / "jokes.json"
USER_JOKES_FILE = Path.home() / ".config" / "claude-buddy" / "jokes.txt"
USER_JOKE_CHANCE = 0.3  # share of jokes taken from your file when it has some
MOMENT_CHANCE = 0.2  # share of jokes about the moment instead of a topic
TOPIC_MEMORY = 6  # a topic does not come back within this many jokes

TOPICS = [
    "Git e conflitos de merge", "café", "reuniões que podiam ser um e-mail", "deploy na sexta-feira",
    "bugs que somem quando alguém olha", "código legado", "estimativas de prazo", "Stack Overflow",
    "chimarrão", "linguagens de programação", "inteligência artificial", "home office",
    "testes automatizados", "documentação", "senhas e segurança", "CSS e centralizar uma div",
    "banco de dados e SQL", "servidores e nuvem", "teclado e mouse", "requisitos que mudam toda hora",
    "nomes de variáveis", "Wi-Fi e internet lenta", "segunda-feira", "churrasco gaúcho",
    "o frio do Rio Grande do Sul",
]
FORMATS = [
    "pergunta e resposta, com a graça na resposta",
    "um trocadilho",
    "uma frase curta de efeito",
    "\"Qual é o cúmulo de...?\" com a resposta",
    "um mini diálogo de duas falas",
    "\"O que é um pontinho <cor> <em algum lugar>?\" e a resposta, que explica o pontinho com uma imagem absurda "
    "ou um trocadilho que encaixa de verdade, como nos clássicos: \"O que é um pontinho verde no canto da sala? "
    "Uma ervilha de castigo.\", \"O que é um pontinho vermelho no castelo? A pimenta do reino.\", \"O que é um "
    "pontinho preto no avião? Uma aeromosca.\". Invente um novo, não use esses",
]

SYSTEM = """You are Buddy, a small pixel-art mascot on a developer's desk in Canoas, Brazil, who watches their Claude Code sessions.

Now and then you tell one short joke. Make it an actual joke with a punchline that lands at the end, the kind a coworker laughs out loud at. Light and upbeat; nothing dark or heavy (no depression, death, illness, insults or politics).

Each request gives you the topic and the format: stick to both. Avoid worn-out setups like "why did X go to the doctor / psychologist / therapy". Never repeat, rephrase or reuse the punchline of a joke from the "already told" list.

Write it in Brazilian Portuguese only (no English words), at most 15 words. It is shown in a small speech bubble and read aloud, so plain text only: no quotes, emojis, hashtags or preamble."""


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
]


def moment(snapshot: dict, weather: dict | None) -> list[str]:
    """Details of the moment a joke may riff on: counts and statuses only, no session content."""
    sessions = [s for s in snapshot.get("sessions", []) if s.get("status") != "offline"]
    working = sum(1 for s in sessions if s.get("status") == "working")
    hour = time.localtime().tm_hour
    period = "madrugada" if hour < 6 else "manhã" if hour < 12 else "tarde" if hour < 18 else "noite"
    parts = [f"agora é {period} ({hour}h)", f"{len(sessions)} sessões abertas, {working} trabalhando"]
    if weather:
        parts.append(f"lá fora {weather['temp']}°C, {weather['text']}")
    return parts


_CHOICE = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def load_user_jokes(path: Path) -> list[str]:
    """Lines of the jokes file, without blanks, # comments and too-long ones."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    jokes, too_long = [], 0
    for line in lines:
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        longest = _CHOICE.sub(lambda m: max(m.group(1).split("|"), key=len), line)
        if len(longest) > JOKE_MAX_CHARS:
            too_long += 1
            continue
        jokes.append(line)
    if too_long:
        log.info("%d jokes in %s are longer than %d characters; skipped", too_long, path, JOKE_MAX_CHARS)
    return jokes


def vary(template: str, rng: random.Random) -> str:
    """Pick one option of every {a|b|c} in a joke."""
    return _CHOICE.sub(lambda m: rng.choice(m.group(1).split("|")).strip(), template)


class JokeTeller:
    def __init__(self, api_key: str | None = None, timeout: float = 8.0, path: Path | None = None,
                 rng: random.Random | None = None, user_path: Path | None = None):
        self.path = path or JOKES_FILE  # looked up at call time (tests patch it)
        self.user_path = user_path or USER_JOKES_FILE
        self.rng = rng or random.Random()
        self.recent: list[str] = []
        self.topics: list[str] = []
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self.recent, self.topics = list(stored.get("jokes", [])), list(stored.get("topics", []))
        except (OSError, ValueError, AttributeError):
            pass
        self._client = None
        key = api_key if api_key is not None else load_key()
        if key:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout, max_retries=1)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"jokes": self.recent, "topics": self.topics}, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
        except OSError:
            log.warning("could not save %s", self.path)

    def _remember(self, joke: str) -> str:
        self.recent = (self.recent + [joke])[-20:]
        self._save()
        return joke

    def _from_list(self, jokes: list[str]) -> str:
        # Remember the template, not the variation, so "{a|b}" lines rotate too.
        fresh = [j for j in jokes if j not in self.recent] or jokes
        template = self.rng.choice(fresh)
        self._remember(template)
        return vary(template, self.rng)

    def _fallback(self) -> str:
        return self._from_list(load_user_jokes(self.user_path) or FALLBACK)

    def pick(self, snapshot: dict, weather: dict | None) -> tuple[str, str]:
        """(topic line, format) for the next joke; remembers the topic."""
        if self.rng.random() < MOMENT_CHANCE:
            detail = self.rng.choice(moment(snapshot, weather))
            topic, line = "o momento", f"o momento ({detail})"
        else:
            fresh = [t for t in TOPICS if t not in self.topics[-TOPIC_MEMORY:]] or TOPICS
            topic = line = self.rng.choice(fresh)
        self.topics = (self.topics + [topic])[-TOPIC_MEMORY:]
        return line, self.rng.choice(FORMATS)

    async def tell(self, snapshot: dict, weather: dict | None) -> str:
        if not self._client:
            return self._fallback()
        mine = load_user_jokes(self.user_path)
        if mine and self.rng.random() < USER_JOKE_CHANCE:
            return self._from_list(mine)
        import anthropic

        topic, form = self.pick(snapshot, weather)
        told = "\n".join(f"- {j}" for j in self.recent[-15:]) or "(nenhuma ainda)"
        content = f"Tema: {topic}\nFormato: {form}\n\nJá contadas:\n{told}"
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
