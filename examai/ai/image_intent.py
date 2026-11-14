"""Keep an explicit user instruction when a screen region becomes the next input."""

import re


EXPLAIN_CODE = "EXPLAIN_CODE"
AUTO_IMAGE = "AUTO_IMAGE"

SCREEN_INSTRUCTION = re.compile(
    r"виділ|скрін|екран|зображ|фото|поясн|объясн|розв[’']?яж|реши|"
    r"прочитай|розбери|що робить|для чого|explain|solve|read", re.IGNORECASE,
)
CODE_REFERENCE = re.compile(
    r"\b(?:код\w*|code|рядок|рядку|рядка|строк\w*|line|оператор\w*|"
    r"функц\w*|програм\w*|scanf|printf|if|for|while)\b", re.IGNORECASE,
)


def select_screen_instruction(current: str, previous_manual: str = "") -> str:
    for candidate in (current, previous_manual):
        candidate = candidate.strip()
        if candidate.startswith("Зображення з екрана"):
            continue
        if candidate and SCREEN_INSTRUCTION.search(candidate):
            return candidate[:1500]
    return ""


def image_intent(instruction: str) -> str:
    return EXPLAIN_CODE if CODE_REFERENCE.search(instruction) else AUTO_IMAGE
