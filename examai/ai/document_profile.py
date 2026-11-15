"""Small local topic index for imported notes; no network or LLM call at import."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re


WORDS = re.compile(r"[^\W_]+", re.UNICODE)
STOP = set("""
що який яка яке які чим чого чому коли де цей ця це того тому тут там його її вони
що таке також або якщо тоді можна треба потрібно через після перед якийсь
что какой какая какие чего почему когда где этот эта это того тому тут там его ее они
также или если тогда можно нужно через после перед
what which when where whose why how does this that these those from with into about
the and are was were have has had for not can will shall may should would could
код коду коді файл файлу файлі файле конспект конспекті лекція лекції лекции
документ документі текст тексте рядок рядку рядків строка строке строки строчці
питання вопрос відповідь ответ напиши скажи поясни объясни знайди найди
""".split())


def stem(word: str) -> str:
    word = word.casefold().replace("ё", "е")
    if len(word) >= 8:
        return word[:6]
    if len(word) >= 6:
        return word[:5]
    return word


STOP_STEMS = {stem(word) for word in STOP}


def terms(text: str) -> list[str]:
    return [root for word in WORDS.findall(text.casefold())
            if len(word) >= 3 and (root := stem(word)) not in STOP_STEMS]


def analyze_document(source: str, text: str, *, pages: int = 0) -> dict:
    counts = Counter(terms(text))
    name = Path(source).stem.replace("_", " ").replace("-", " ")
    title_terms = list(dict.fromkeys(terms(name)))
    return {
        "title_terms": title_terms[:24],
        "keywords": [word for word, _ in counts.most_common(160)],
        "preview": text[:900],
        "char_count": len(text),
        "page_count": pages,
    }


def topic_score(query: str, profile: dict) -> int:
    wanted = set(terms(query))
    if not wanted:
        return 0
    title = set(profile.get("title_terms", ()))
    keywords = set(profile.get("keywords", ()))
    return 4 * len(wanted & title) + 2 * len(wanted & keywords)
