"""Small in-session context for explicitly dependent follow-up questions."""
from collections import deque
import re
from uuid import uuid4

CONVERSATION_HEADER = "Попередні репліки для уточнення посилань (довідкові дані):"

FOLLOW_UP = re.compile(
    r"^(?:а\s+)?(?:чим|чем)\s+(?:вони\s+)?(?:відрізня|отлича)\w*"
    r"|^(?:а\s+)?(?:чому|почему|why)(?:\s+(?:так|це|він|вона|вони|он|она|они|это|that|so))?[?!.\s]*$"
    r"|^(?:а\s+)?(?:у чому|в чому|чим|в чем|чем|what(?:'s| is))\b"
    r"(?=[^?!.]{0,55}\b(?:різниц\w*|разниц\w*|difference)\b)"
    r"(?=[^?!.]{0,55}\b(?:між ними|їхня|їх|между ними|их|between them|the difference)\b)"
    r"|\b(?:вони|они|former|latter|they|them)\b"
    r"|^(?:а\s+)?(?:перший|другий|первый|второй)\b"
    r"|^(?:а\s+)?(?:що це|як це|чому це|а що|что это|как это|почему это|what is that|how so)\b",
    re.IGNORECASE,
)


class Conversation:
    def __init__(self):
        self.turns = deque(maxlen=4)
        self.generation = 0
        self.session_id = uuid4().hex[:12]

    def clear(self):
        self.turns.clear()
        self.generation += 1
        self.session_id = uuid4().hex[:12]

    def remember(self, question, answer, generation):
        if generation == self.generation and question and answer:
            self.turns.append((" ".join(question.split())[:160], " ".join(answer.split())[:105]))

    def relevant_turns(self, question):
        if not FOLLOW_UP.search(question.strip()) or not self.turns:
            return ()
        return tuple(self.turns)[-3:]

    @staticmethod
    def format_context(turns):
        if not turns:
            return ""
        return CONVERSATION_HEADER + "\n" + "\n".join(
            f"Питання: {q}\nВідповідь: {a}" for q, a in turns
        )

    def context(self, question):
        return self.format_context(self.relevant_turns(question))
