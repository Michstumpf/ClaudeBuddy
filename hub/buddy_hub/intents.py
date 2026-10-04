"""Voice intents in a dictation: send to a session, ask about one, approve/deny.

The hub checks every transcription for these before the text is typed into
the focused window; a recognized intent is handled by the hub instead.
Matching is tolerant of how Whisper writes things: accents, punctuation and
spacing are ignored ("data hub" matches "DataHub Sharing Chat").
"""

import re
import unicodedata
from dataclasses import dataclass

SEND_VERBS = ("manda", "mande", "envia", "envie", "escreve", "escreva", "pede", "peca", "fala", "diz", "diga")
TO = ("para", "pra", "pro", "na", "no", "ao", "a")
ARTICLES = ("a", "o", "sessao", "da", "do")
APPROVE = ("pode aprovar", "aprova", "aprovado", "pode seguir", "autoriza", "autorizado")
DENY = ("nega", "negado", "recusa", "nao aprova", "nao autoriza", "cancela o pedido")
MIN_MATCH = 4  # characters of a session name that must match
STATUS_PATTERNS = (
    r"^o que (?:a |o )?(?:sessao )?(.+?) (?:esta|ta) fazendo$",
    r"^como (?:esta|ta|anda|vai) (?:a |o )?(?:sessao )?(.+)$",
    r"^status (?:da |do |de )?(?:sessao )?(.+)$",
)


def fold(text: str) -> str:
    """Lowercase, no accents, punctuation as spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def squash(text: str) -> str:
    return fold(text).replace(" ", "")


@dataclass
class Intent:
    kind: str                 # "send" | "status" | "approve" | "deny"
    session_id: str = ""
    session_name: str = ""
    body: str = ""            # text to deliver, for "send"


def match_session(words: list[str], sessions: list[tuple[str, str]]) -> tuple[tuple[str, str], int] | None:
    """The session whose name the words start with, and how many words that took.

    sessions: (id, name). Compares squashed text, so "data hub" == "DataHub";
    a spoken prefix of the name is enough ("hipaa" for "HIPAA Compliance").
    """
    best = None
    for sid, name in sessions:
        key = squash(name)
        if not key:
            continue
        spoken = ""
        for count, word in enumerate(words, start=1):
            if word in ("e", "and") and spoken and not key.startswith(spoken + word):
                continue  # "Ideas & Votes" is said "ideas and votes"
            spoken += word
            if not key.startswith(spoken):
                break
            if len(spoken) >= min(MIN_MATCH, len(key)) and (best is None or len(spoken) > best[2]):
                best = ((sid, name), count, len(spoken))
    return (best[0], best[1]) if best else None


def _strip_lead(words: list[str]) -> list[str]:
    while words and words[0] in ("buddy", "ei", "oi", "por", "favor"):
        words = words[1:]
    return words


def _original_tail(text: str, skip_words: int) -> str:
    """The original text after its first skip_words words (keeps accents and case)."""
    parts = re.findall(r"\S+", text)
    kept, folded_seen = [], 0
    for part in parts:
        n = len(fold(part).split())
        if folded_seen >= skip_words:
            kept.append(part)
        folded_seen += n
    return " ".join(kept).lstrip(":,;-–— ").strip()


def parse(text: str, sessions: list[tuple[str, str]]) -> Intent | None:
    words = _strip_lead(fold(text).split())
    if not words:
        return None
    lead = len(fold(text).split()) - len(words)
    joined = " ".join(words)

    # "pode aprovar" / "nega": short utterances only
    if len(words) <= 5:
        if any(joined == p or joined.startswith(p + " ") for p in DENY):
            return Intent("deny")
        if any(joined == p or joined.startswith(p + " ") for p in APPROVE):
            return Intent("approve")

    # "manda para a HIPAA: roda os testes"
    if words[0] in SEND_VERBS and len(words) >= 3 and words[1] in TO:
        rest = words[2:]
        while rest and rest[0] in ARTICLES:
            rest = rest[1:]
        found = match_session(rest, sessions)
        if found:
            (sid, name), used = found
            consumed = lead + (len(words) - len(rest)) + used
            body = _original_tail(text, consumed)
            body = re.sub(r"^(que|e)\s+", "", body, flags=re.I).strip()
            if body:
                return Intent("send", sid, name, body)

    # "o que a DataHub está fazendo?" / "como está a HIPAA?" / "status da git-d6"
    for pattern in STATUS_PATTERNS:
        m = re.match(pattern, joined)
        if m:
            found = match_session(m.group(1).split(), sessions)
            if found:
                (sid, name), _ = found
                return Intent("status", sid, name)
    return None
