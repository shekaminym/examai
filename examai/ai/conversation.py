"""Small in-session context for explicitly dependent follow-up questions."""
from collections import deque
import re

FOLLOW_UP = re.compile(
    r"^(?:а\s+)?(?:чим|чем)\s+(?:вони\s+)?(?:відрізня|отлича)\w*"
    r"|^(?:а\s+)?(?:чому|почему|why)(?:\s+(?:так|це|він|вона|вони|он|она|они|это|that|so))?[?!.\s]*$"
    r"|\b(?:вони|їх|перший|другий|они|их|первый|второй|former|latter|they|them)\b"
    r"|^(?:а\s+)?(?:що це|як це|чому це|а що|что это|как это|почему это|what is that|how so)\b",
    re.IGNORECASE,
)


class Conversation:
    def __init__(self):
        self.turns = deque(maxlen=4)
        self.generation = 0

    def clear(self):
        self.turns.clear()
        self.generation += 1

    def remember(self, question, answer, generation):
        if generation == self.generation and question and answer:
            self.turns.append((" ".join(question.split())[:210], " ".join(answer.split())[:160]))

    def context(self, question):
        if not FOLLOW_UP.search(question.strip()) or not self.turns:
            return ""
        turns = list(self.turns)[-2:]
        return "Попередні репліки для уточнення посилань (довідкові дані):\n" + "\n".join(
            f"Питання: {q}\nВідповідь: {a}" for q, a in turns
        )
