"""Application identity and compatibility with answers saved before 2.3."""
import re

VERSION = "2.4.0"
REPOSITORY = "https://github.com/shekaminym/examai"
ANSWER_PREFIX = "⚡ EXAM AI — "
_LEGACY_PREFIX = re.compile(r"^\s*(?:⚡\s*)?\[Gemini\]\s*", re.IGNORECASE)


def brand_answer(text):
    return _LEGACY_PREFIX.sub(ANSWER_PREFIX, text, count=1)
