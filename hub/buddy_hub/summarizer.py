"""Spoken summary of a Claude Code answer, written by Claude Haiku.

Reading the first sentences of an answer written for a screen sounds stiff;
Haiku rewrites it as one or two sentences meant to be heard. On any failure
(no key, timeout, API error) the caller falls back to tts.speakable().

The key must be the one approved for Portrait work: voice turns can happen in
Portrait sessions, so the answer text is Portrait context.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("buddy.summarizer")

KEY_FILE = Path.home() / ".config" / "claude-buddy" / "anthropic_key"
MODEL = "claude-haiku-4-5"
# Answers can be long; the start carries the conclusion, the tail rarely matters
# for a spoken line, and a smaller input keeps latency down.
ANSWER_MAX_CHARS = 6000

SYSTEM = """You voice a coding assistant's answers for a desk gadget that speaks them aloud.

You get the user's question and the assistant's full written answer. Reply with what the gadget should say: one or two short sentences (at most about 40 words) in Brazilian Portuguese, spoken and natural, like a colleague giving a quick update.

- Lead with the outcome: done or not, what changed, what it found, or what it needs from the user.
- If the answer asks the user a question or needs a decision, end with that question.
- Never read code, commands, file paths, URLs, IDs, hashes or long numbers; refer to them in words ("o arquivo de configuração", "o commit").
- Plain text only: no markdown, lists, emojis or quotes.
- Say only the line itself, with no preamble."""


def load_key() -> str | None:
    key = os.environ.get("BUDDY_ANTHROPIC_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    return key or None


class Summarizer:
    def __init__(self, api_key: str | None, timeout: float = 6.0):
        self.enabled = bool(api_key)
        self._client = None
        if api_key:
            import anthropic

            # Short timeout and a single retry: a spoken reply that arrives late
            # is worse than the plain fallback.
            self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)

    async def summarize(self, question: str, answer: str) -> str | None:
        """The line to speak, or None to fall back to the plain extract."""
        if not self._client:
            return None
        import anthropic

        content = f"<question>\n{question.strip()}\n</question>\n\n<answer>\n{answer.strip()[:ANSWER_MAX_CHARS]}\n</answer>"
        try:
            response = await self._client.messages.create(
                model=MODEL,
                max_tokens=200,  # deliberately short: one or two spoken sentences
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.RateLimitError:
            log.warning("haiku rate limited; using the plain extract")
            return None
        except anthropic.APIStatusError as exc:
            log.warning("haiku returned %s (%s); using the plain extract", exc.status_code, getattr(exc, "type", "?"))
            return None
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            log.warning("haiku unreachable (%s); using the plain extract", exc.__class__.__name__)
            return None
        if response.stop_reason not in ("end_turn", "max_tokens"):
            return None
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text or None
