"""Bounded, punctuation-aware assembly of VAD/STT fragments into questions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from time import perf_counter


LEADING_FILLER = re.compile(
    r"^(?:(?:[еэєма]{2,}|[еэєм](?=[,.…])|ем+|эм+|гм+|хм+|ну|угу|ага)"
    r"[\s,.…-]+)+", re.IGNORECASE
)
TRAILING_OPEN = re.compile(r"\b(?:і|й|та|або|чи|що|який|яка|яке|як|для|не|з|із|від|и|или|что|как|для|с|and|or)\s*[,.…?]*$", re.IGNORECASE)
QUESTION_START = re.compile(r"^(?:що|чому|навіщо|як|який|яка|яке|де|коли|хто|чи|поясни|що\s+таке|что|почему|как|где|зачем|what|why|how)\b", re.IGNORECASE)
CONTINUATION_START = re.compile(r"^(?:і|й|та|або|и|или|а|and|or)\b", re.IGNORECASE)
FILLER_SEQUENCE = re.compile(r"(?:[еэєма]{2,}|ем+|эм+|гм+|хм+|ну|угу|ага|тобто|то есть)", re.I)
BARE_OPENERS = {"що таке", "что такое", "what is", "що це таке", "что это такое",
                "поясни", "объясни", "розкажи", "расскажи", "наприклад", "например"}
NOISE = {"е", "ээ", "ее", "мм", "м", "аа", "а", "ну", "так", "це", "эм", "угу"}


def clean_fragment(text: str) -> str:
    text = " ".join(text.strip().split())
    text = LEADING_FILLER.sub("", text).strip(" ,.…")
    return text


def is_noise(text: str) -> bool:
    plain = re.sub(r"[^\w]+", "", text.casefold())
    repeated = re.sub(r"\b([еэєма])(?:[-–]\1)+\b", r"\1\1", text.casefold())
    tokens = re.findall(r"[^\W_]+", re.sub(r"\bто есть\b", "тобто", repeated))
    return (not plain or plain in NOISE or
            bool(tokens and all(FILLER_SEQUENCE.fullmatch(token) for token in tokens)))


def is_open_fragment(text: str) -> bool:
    clean = clean_fragment(text).rstrip(" ?!.,…").casefold()
    return (text.rstrip().endswith(("...", "…")) or bool(TRAILING_OPEN.search(clean))
            or clean in BARE_OPENERS)


def is_unreliable_transcript(text: str) -> bool:
    """Reject obvious fragments; keep unfamiliar complete technical terms intact."""
    clean = clean_fragment(text)
    if is_noise(clean) or is_open_fragment(clean):
        return True
    words = re.findall(r"[^\W_]+", clean, re.UNICODE)
    if len(words) == 1 and len(words[0]) < 4 and not words[0].isupper() and not clean.endswith("?"):
        return True
    return bool(words and len(words[-1]) == 1 and words[-1].isalpha()
                and not clean.endswith("?"))


def is_continuation_fragment(text: str) -> bool:
    return bool(CONTINUATION_START.search(clean_fragment(text)))


def join_fragments(previous: str, fragment: str) -> str:
    if not previous:
        return fragment
    left = previous.rstrip(" .,…?!")
    right = fragment.lstrip(" ,.…")
    if right.casefold() == left.casefold() or right.casefold().startswith(left.casefold() + " "):
        return right
    if left.casefold().startswith(right.rstrip(" .,…?!").casefold() + " "):
        return previous
    left_words = left.split()
    right_words = right.split()
    for overlap in range(min(5, len(left_words), len(right_words)), 0, -1):
        if [word.casefold().strip(",.?!") for word in left_words[-overlap:]] == [
            word.casefold().strip(",.?!") for word in right_words[:overlap]
        ]:
            right_words = right_words[overlap:]
            break
    if not right_words:
        return previous
    return left + " " + " ".join(right_words)


@dataclass(frozen=True)
class AssembledQuestion:
    text: str
    request_id: str
    speech_ended_at: float | None
    fragments: int


class QuestionAggregator:
    def __init__(self, *, mode="normal", max_wait=None):
        self.mode = mode if mode in {"normal", "slow", "complete", "auto"} else "normal"
        limits = {"normal": 8.0, "slow": 20.0, "complete": 22.5, "auto": 12.0}
        self.max_wait = limits[self.mode] if max_wait is None else max_wait
        self.clear()

    def clear(self):
        self.text = ""
        self.request_id = ""
        self.speech_ended_at = None
        self.started_at = None
        self.deadline = None
        self.fragments = 0
        self.speech_active = False
        self._last_final_at = None
        self._observed_gap = 0.0
        self._partial_open = False

    @property
    def pending(self):
        return bool(self.text)

    def add_final(self, text, speech_ended_at=None, request_id="", *, now=None):
        now = perf_counter() if now is None else now
        cleaned = clean_fragment(text)
        if is_noise(cleaned):
            if self.pending:
                self.speech_active = False
                self.deadline = min(self.started_at + self.max_wait,
                                    max(self.deadline, now + 0.9))
            return False
        if not self.pending:
            self.started_at = now
            self.request_id = request_id
        merged = join_fragments(self.text, cleaned)
        if merged == self.text:
            if self.speech_active:
                self.speech_active = False
                self.deadline = min(self.deadline, now + 0.52)
            return False
        self.text = merged
        self.speech_ended_at = speech_ended_at
        self.fragments += 1
        self.speech_active = False
        waits = {"normal": (2.2, 0.65, 1.1),
                 "slow": (5.0, 3.0, 3.5),
                 "complete": (3.25, 2.0, 2.25),
                 "auto": (2.6, 0.52, 1.15)}[self.mode]
        wait = waits[0] if is_open_fragment(text) else waits[1] if cleaned.endswith("?") else waits[2]
        if self.mode == "complete" and self.fragments > 1:
            wait = max(wait, min(3.5, self._observed_gap * 0.625 + 0.3))
        if self.mode == "auto":
            wait = max(wait, min(3.4, self._observed_gap * 1.3 + 0.35))
            if self._partial_open and not cleaned.endswith("?"):
                wait = max(wait, 1.5)
        self._partial_open = False
        self.deadline = min(self.started_at + self.max_wait, now + wait)
        self._last_final_at = now
        return True

    def speech_started(self, *, now=None):
        if self.pending:
            now = perf_counter() if now is None else now
            if self._last_final_at is not None:
                gap = max(0.0, now - (self.speech_ended_at or self._last_final_at))
                self._observed_gap = self._observed_gap * 0.35 + gap * 0.65
            self.speech_active = True
            # Wait for the new VAD segment's final STT result, with a hard cap.
            self.deadline = self.started_at + self.max_wait

    def preview(self, text):
        cleaned = clean_fragment(text)
        if is_noise(cleaned):
            return self.text
        if self.mode == "auto":
            self._partial_open = is_open_fragment(text)
        return join_fragments(self.text, cleaned)

    def ready(self, *, now=None):
        now = perf_counter() if now is None else now
        return (self.pending and self.deadline is not None and now >= self.deadline
                and (self.mode == "auto" or not self.speech_active))

    def finalize(self, *, force=False, now=None):
        if not self.pending or (not force and not self.ready(now=now)):
            return None
        text = self.text.strip()
        if not text.endswith(("?", "!", ".")) and QUESTION_START.search(text) and not is_open_fragment(text):
            text += "?"
        question = AssembledQuestion(text, self.request_id, self.speech_ended_at, self.fragments)
        self.clear()
        return question
