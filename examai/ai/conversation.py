"""Small in-session context for explicitly dependent follow-up questions."""
import re
from uuid import uuid4

CONVERSATION_HEADER = "Попередні репліки для уточнення посилань (довідкові дані):"
TOPIC_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
TOPIC_STOP = set("що таке які який яка яке чим вони відрізняються чому як для про "
                 "детальніше докладніше розкажи поясни подробнее расскажи объясни "
                 "что такое какие какой какая чем они отличаются почему как для про "
                 "what which are the does do how why is of and a an".split())
ELABORATION = re.compile(r"детальніше|докладніше|подробнее|tell me more|explain further|elaborate", re.I)

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
    r"|^(?:а\s+)?(?:детальніше|докладніше|подробнее|tell me more|explain further)\b"
    r"|^(?:а\s+)?(?:розкажи|поясни|расскажи|объясни)\s+(?:детальніше|докладніше|подробнее)\b"
    r"|\b(?:ця змінна|цю змінну|цей рядок|ту строку|эта переменная|эту переменную|"
    r"this variable|that variable|this line|that line|у нього|у него)\b"
    r"|^(?:а\s+)?(?:для чого|навіщо|де|як|чому|що робить|зачем|для чего|где|как|why|where|what)\s+"
    r"(?:це|він|вона|воно|її|його|їх|он|она|оно|это|её|его|it|this|that)\b"
    r"|\b(?:якщо|если|if)\s+(?:її|його|їх|её|его|их|it|them)\b"
    r"|^(?:а\s+)?(?:покажи|знайди|покажіть|найди|show|find)\b[^?!.]{0,45}"
    r"\b(?:вона|він|його|її|це|она|он|его|её|это|it)\b"
    r"|^(?:а\s+)?(?:що це|як це|чому це|а що|что это|как это|почему это|what is that|how so)\b",
    re.IGNORECASE,
)
COMPARISON = re.compile(r"різниц|відрізня|порівняй|разниц|отлича|сравни|differ|compare", re.I)
COMPARISON_REFERENCE = re.compile(r"\b(?:вони|ними|їх|їхня|они|ними|их|them|they|former|latter)\b", re.I)
CONTEXT_LIMIT = 1900
CODE_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
USAGE_LOCATION = re.compile(r"\b(?:де|где|where)\b", re.I)
CONTEXT_SOURCE = re.compile(r"файл[у]? «([^»]+)»", re.I)


class Conversation:
    def __init__(self):
        self.turns = []
        self._summaries = []
        self._topics = []
        self._evidence = []
        self.generation = 0
        self.session_id = uuid4().hex[:12]

    def clear(self):
        self.turns.clear()
        self._summaries.clear()
        self._topics.clear()
        self._evidence.clear()
        self.generation += 1
        self.session_id = uuid4().hex[:12]

    def remember(self, question, answer, generation, evidence=""):
        if generation == self.generation and question and answer:
            self.turns.append((question, answer))
            self._summaries.append((" ".join(question.split())[:170], " ".join(answer.split())[:245]))
            self._topics.append(self._terms(question))
            self._evidence.append(evidence[:1400])

    @staticmethod
    def _terms(text):
        # Prefixes keep common case inflections searchable with bounded local work.
        return {word[:5] for word in TOPIC_WORDS.findall(text.casefold())
                if len(word) >= 4 and word not in TOPIC_STOP}

    def _selected_indices(self, question):
        if not self.turns:
            return []
        follow_up = FOLLOW_UP.search(question.strip()) or ELABORATION.search(question)
        terms = self._terms(question)
        related = [i for i in range(len(self.turns) - 1, -1, -1) if terms & self._topics[i]][:3]
        if not follow_up:
            return sorted(related)
        if COMPARISON.search(question):
            anchors = [i for i in range(len(self.turns) - 1, -1, -1)
                       if not FOLLOW_UP.search(self.turns[i][0])][:3]
            if related:
                anchors = (related + [i for i in anchors if i not in related])[:3]
            if len(anchors) == 3 and not (self._topics[anchors[2]] &
                                            (self._topics[anchors[0]] | self._topics[anchors[1]])):
                anchors.pop()
            latest = len(self.turns) - 1
            if FOLLOW_UP.search(self.turns[latest][0]):
                anchors.append(latest)
            return sorted(set(anchors))
        recent = [len(self.turns) - 1]
        while recent[-1] > 0 and len(recent) < 3 and FOLLOW_UP.search(self.turns[recent[-1]][0]):
            recent.append(recent[-1] - 1)
        # Keep the original subject even after several rounds of pronouns.
        anchor = recent[-1]
        while anchor > 0 and FOLLOW_UP.search(self.turns[anchor][0]):
            anchor -= 1
        essential = [recent[0], anchor]
        if anchor and any(COMPARISON.search(self.turns[i][0]) for i in range(anchor + 1, len(self.turns))):
            previous = next((i for i in range(anchor - 1, -1, -1)
                             if not FOLLOW_UP.search(self.turns[i][0])), None)
            if previous is not None:
                essential.append(previous)
        return sorted(list(dict.fromkeys(essential + related + recent))[:4])

    def relevant_turns(self, question):
        selected = self._selected_indices(question)
        return tuple(self._summaries[i] if i < len(self.turns) - 3 else
                     (self._summaries[i][0], " ".join(self.turns[i][1].split())[:660])
                     for i in selected)

    @staticmethod
    def resolve_comparison(question, turns):
        """Name the referents in the question itself before it reaches either transport."""
        if not (COMPARISON.search(question) and COMPARISON_REFERENCE.search(question)):
            return question
        topics = []
        for previous, _ in turns:
            if FOLLOW_UP.search(previous) or ELABORATION.search(previous):
                continue
            label = re.sub(r"^(?:що таке|что такое|what is|what are)\s+", "", previous, flags=re.I)
            label = label.strip(" ?.!")
            if label and label.casefold() not in {topic.casefold() for topic in topics}:
                topics.append(label)
        if len(topics) < 2:
            return question
        return f"Порівняй ці поняття: {'; '.join(topics)}. Поясни їхню відмінність і зв’язок."

    def reference_context(self, question):
        if (not self.turns or not (FOLLOW_UP.search(question.strip()) or ELABORATION.search(question))
                or COMPARISON.search(question)):
            return ""
        return next((self._evidence[i] for i in reversed(self._selected_indices(question)) if self._evidence[i]), "")

    @staticmethod
    def code_usage_reference(question, turns, evidence):
        if not (FOLLOW_UP.search(question) and USAGE_LOCATION.search(question)):
            return None
        source = CONTEXT_SOURCE.search(evidence)
        identifiers = set(CODE_IDENTIFIER.findall(" ".join(answer for _, answer in turns)))
        if source and len(identifiers) == 1:
            return identifiers.pop(), source[1]
        return None

    @staticmethod
    def format_context(turns):
        if not turns:
            return ""
        limit = max(0, (CONTEXT_LIMIT - len(CONVERSATION_HEADER) - 12) // len(turns) - 27)
        return CONVERSATION_HEADER + "\n" + "\n".join(
            f"Питання: {q}\nВідповідь: {a[:max(0, limit - len(q))]}" for q, a in turns
        )

    def context(self, question):
        return self.format_context(self.relevant_turns(question))
