"""Fast, deliberately conservative corrections for known ASR confusions."""
from __future__ import annotations

import json
import re
from pathlib import Path


UKRAINIAN = "А-Яа-яІіЇїЄєҐґ"
APOSTROPHE = re.compile(rf"(?<=[{UKRAINIAN}])['ʼʻʹ’′`´](?=[{UKRAINIAN}])")
WORD = re.compile(rf"(?<!\w)[{UKRAINIAN}’]{{7,}}(?!\w)")
NON_UKRAINIAN_CYRILLIC = frozenset("ЫыЭэЁёЪъ")


def has_non_ukrainian_cyrillic(text: str) -> bool:
    return any(char in NON_UKRAINIAN_CYRILLIC for char in text)


def normalize_apostrophes(text: str) -> str:
    return APOSTROPHE.sub("’", text)


def edit_distance(left: str, right: str, limit: int = 2) -> int:
    """Small bounded distance for a handful of known subject terms."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, 1):
        current = [row]
        for column, other in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (char != other),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def match_case(sample: str, replacement: str) -> str:
    if sample.isupper():
        return replacement.upper()
    return replacement.capitalize() if sample[0].isupper() else replacement


class SpeechCorrector:
    def __init__(self, filepath: str | Path = "speech_glossary.json") -> None:
        self.filepath = Path(filepath)
        self._rules: list[tuple[re.Pattern[str], str]] = []
        self.terms: tuple[str, ...] = ()
        self._fuzzy_terms: tuple[tuple[str, str], ...] = ()
        self.reload()

    def reload(self) -> None:
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        rules = []
        terms = []
        for wrong, right in sorted(data.items(), key=lambda item: -len(item[0])):
            if not (isinstance(wrong, str) and isinstance(right, str)):
                continue
            wrong, right = normalize_apostrophes(wrong.strip()), normalize_apostrophes(right.strip())
            if not wrong or not right or len(wrong) > 80 or len(right) > 80:
                continue
            # Match complete words and phrases only. Never alter substrings of
            # unrelated words or invent a correction from a fuzzy similarity.
            pattern = re.compile(r"(?<!\w)" + re.escape(wrong).replace(r"\ ", r"\s+") + r"(?!\w)", re.IGNORECASE)
            rules.append((pattern, right))
            if right not in terms and not any(char in right for char in "<>\r\n"):
                terms.append(right)
        self._rules = rules
        self.terms = tuple(terms[:30])
        self._fuzzy_terms = tuple(
            (term.replace("’", "").casefold(), term)
            for term in self.terms if "’" in term and " " not in term and "-" not in term
        )

    def correct(self, text: str) -> str:
        text = normalize_apostrophes(text)
        for pattern, replacement in self._rules:
            text = pattern.sub(lambda match: match_case(match.group(), replacement), text)

        def repair(match: re.Match[str]) -> str:
            word = match.group()
            compact = word.replace("’", "").casefold()
            candidates = []
            for target, canonical in self._fuzzy_terms:
                if compact[0] != target[0] or compact[-2:] != target[-2:]:
                    continue
                distance = edit_distance(compact, target)
                if distance <= 2:
                    candidates.append((distance, canonical))
            if not candidates:
                return word
            candidates.sort(key=lambda item: item[0])
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                return word
            return match_case(word, candidates[0][1])

        return WORD.sub(repair, text)
