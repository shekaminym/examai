"""Small in-session context for explicitly dependent follow-up questions."""
from collections import deque
from difflib import SequenceMatcher
import re
from uuid import uuid4

CONVERSATION_HEADER = "Попередні репліки для уточнення посилань (довідкові дані):"
TOPIC_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
TOPIC_STOP = set("що таке які який яка яке чим вони відрізняються чому як для про "
                 "что такое какие какой какая чем они отличаются почему как для про "
                 "what which are the does do how why is of and a an".split())

FOLLOW_UP = re.compile(
    r"^(?:а\s+)?(?:чим|чем)\s+(?:вони\s+)?(?:відрізня|отлича)\w*"
    r"|^(?:а\s+)?(?:чому|почему|why)(?:\s+(?:так|це|він|вона|вони|он|она|они|это|that|so))?[?!.\s]*$"
    r"|^(?:а\s+)?(?:у чому|в чому|чим|в чем|чем|what(?:'s| is))\b"
    r"(?=[^?!.]{0,55}\b(?:різниц\w*|разниц\w*|difference)\b)"
    r"(?=[^?!.]{0,55}\b(?:між ними|їхня|їх|между ними|их|between them|the difference)\b)"
    r"|\b(?:вони|они|former|latter|they|them)\b"
    r"|^(?:а\s+)?(?:перший|другий|первый|второй)\b"
    r"|\b(?:першого|другого|третього|первого|второго|третьего)\s+(?:виду|вида|типу|типа)\b"
    r"|\b(?:першому|другому|третьому|первом|втором|третьем)\s+(?:випадку|случае)\b"
    r"|\bthe\s+(?:first|second|third)\s+(?:kind|type|case|one)\b"
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
            # A 105-character preview loses the actual list after an opening
            # definition. Keep a bounded answer, still well below FAST's
            # existing 3,500-character context budget for three turns.
            self.turns.append((" ".join(question.split())[:180], " ".join(answer.split())[:650]))

    @staticmethod
    def _related(left, right):
        def terms(text):
            return [word for word in TOPIC_WORDS.findall(text.casefold())
                    if len(word) >= 4 and word not in TOPIC_STOP]
        first, second = terms(left), terms(right)
        return any(a == b or (len(a) >= 6 and len(b) >= 6 and
                              SequenceMatcher(None, a, b).ratio() >= .68)
                   for a in first for b in second)

    def relevant_turns(self, question):
        if not FOLLOW_UP.search(question.strip()) or not self.turns:
            return ()
        selected = []
        for turn in reversed(tuple(self.turns)[-3:]):
            if selected and not (FOLLOW_UP.search(selected[-1][0]) or
                                 self._related(selected[-1][0], turn[0])):
                break
            selected.append(turn)
        return tuple(reversed(selected))

    @staticmethod
    def format_context(turns):
        if not turns:
            return ""
        return CONVERSATION_HEADER + "\n" + "\n".join(
            f"Питання: {q}\nВідповідь: {a}" for q, a in turns
        )

    def context(self, question):
        return self.format_context(self.relevant_turns(question))
