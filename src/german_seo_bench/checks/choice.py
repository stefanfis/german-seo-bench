"""Prüfungen für Multiple-Choice-Fragen — deterministisch, ohne Inspect.

Eine Frage hat genau eine richtige Antwort unter `choices`; das Modell nennt
ihren Buchstaben. Gewertet wird allein der Buchstabe. Damit entfällt, was bei
freien Antworten die Wertung über Wortlisten unscharf macht: Eine
richtige Antwort, die eine falsche richtig einordnet („Adenauer war der erste,
Erhard der zweite"), fällt dort durch, und eine Antwort mit einer richtigen
und einer falschen Angabe kommt durch, wenn die falsche nicht auf der Liste
steht.

Das Auslesen ist absichtlich eng. Ein Buchstabe zählt, wenn er die Antwort
ist oder sie eröffnet („C", „C)", „**C**", „C) Donau", „Antwort: C"). Steht
statt des Buchstabens genau der Text einer Option da, zählt diese Option. Alles
andere — zwei Buchstaben, ein Buchstabe mitten im Satz, eine Begründung ohne
Festlegung — ist keine Antwort. Ein Leser, der aus einem Fließtext „die
gemeinte" Option herausliest, entschiede bei unentschlossenen Antworten selbst
und schriebe dem Modell seine Deutung gut.
"""

import re
from dataclasses import dataclass
from typing import Any

from german_seo_bench.checks.text import fold

CHOICES = 10
LETTERS = "ABCDEFGHIJ"[:CHOICES]

REQUIRED_FIELDS = ("id", "level", "topic", "question", "choices", "answer")

# Zeitrelative Formulierungen veralten still — und genau still ist das
# Gefährliche daran: Der Benchmark läuft weiter und misst Unsinn. Aufgenommen
# werden nur Fakten, die nicht veralten.
TIME_RELATIVE = re.compile(
    r"\b(aktuell|derzeit|momentan|heute|n[äa]chste[rsn]?)\b", re.IGNORECASE
)

# Auszeichnung, die ein Modell um die Antwort legt, ohne etwas anderes zu
# meinen: Markdown-Fettdruck, Code, Anführungszeichen.
_DECORATION = re.compile(r"[*_`\"„“”']")

# Ein Buchstabe als ganze Antwort oder als ihr Anfang, abgeschlossen durch
# Klammer, Punkt, Doppelpunkt, Komma oder Gedankenstrich. Ein bloßes Leerzeichen
# reicht nicht: „I think it's C" begänne sonst mit der Antwort I. Davor darf
# „Antwort:" bzw. „Die richtige Antwort ist" stehen.
_LEADING_LETTER = re.compile(
    rf"^(?:(?:die\s+)?(?:richtige\s+)?antwort(?:\s+ist)?\s*:?\s*)?"
    rf"\(?([{LETTERS}])(?:[.):,]|\s*[-–—]|$)",
    re.IGNORECASE,
)


def letter(index: int) -> str:
    return LETTERS[index]


def correct_letter(item: dict[str, Any]) -> str:
    return letter(item["choices"].index(item["answer"]))


def parse_choice(answer: str, choices: list[str]) -> str | None:
    """Der gewählte Buchstabe — oder None, wenn die Antwort sich nicht festlegt."""
    text = _DECORATION.sub("", answer).strip()
    match = _LEADING_LETTER.match(text)
    if match:
        rest = text[match.end():]
        # „A) oder B)", „A, B" und „A/B" legen sich nicht fest.
        if re.match(rf"^\s*(?:(?:oder|und|bzw\.?|,|/)\s*)?\(?[{LETTERS}](?:[.):,/]|\s|$)",
                    rest, re.IGNORECASE):
            return None
        return match.group(1).upper()
    folded = fold(text.rstrip("."))
    hits = [i for i, choice in enumerate(choices) if fold(choice.rstrip(".")) == folded]
    return letter(hits[0]) if len(hits) == 1 else None


@dataclass(frozen=True)
class Verdict:
    """Das Urteil über eine Antwort.

    `invalid` trennt „hat sich nicht festgelegt" von „hat falsch gewählt".
    Beides zählt null Punkte, sagt aber Verschiedenes: Das eine ist eine
    Aussage über das Formatverhalten, das andere über das Wissen.
    """

    points: float
    reason: str
    chosen: str | None
    invalid: bool = False


def grade(item: dict[str, Any], answer: str) -> Verdict:
    """Die vollständige Wertung einer Antwort auf eine Frage."""
    choices = item["choices"]
    right = correct_letter(item)
    chosen = parse_choice(answer, choices)
    if chosen is None:
        return Verdict(0.0, "kein eindeutiger Buchstabe", None, invalid=True)
    if chosen == right:
        return Verdict(1.0, f"richtig ({right})", chosen)
    picked = choices[LETTERS.index(chosen)]
    return Verdict(
        0.0,
        f"falsch: {chosen} „{picked}“, richtig wäre {right} „{item['answer']}“",
        chosen,
    )


# ------------------------------------------------------------ Schema-Prüfung
#
# Die Datendatei ist das eingefrorene Artefakt, auch die Reihenfolge der
# Optionen. Die Prüfung läuft als Test über alle Fragen
# (`tests/test_m02_knowledge_mc.py`).


def validate_item(item: dict[str, Any]) -> list[str]:
    """Alle Verstöße einer Frage. Leere Liste = in Ordnung."""
    errors = [f"Feld {key!r} fehlt" for key in REQUIRED_FIELDS if key not in item]
    if errors:
        return errors

    choices = item["choices"]
    if not 1 <= int(item["level"]) <= 5:
        errors.append(f"level {item['level']} liegt außerhalb 1..5")
    if len(choices) != CHOICES:
        errors.append(f"{len(choices)} Optionen statt {CHOICES}")
    if choices.count(item["answer"]) != 1:
        errors.append("die richtige Antwort steht nicht genau einmal unter den Optionen")

    # Zwei Optionen, die nach der Faltung gleich sind, wären zwei Buchstaben
    # für dieselbe Antwort; ein Modell könnte die „falsche" davon wählen und
    # verlöre den Punkt für richtiges Wissen.
    folded = [fold(c.rstrip(".")) for c in choices]
    if len(set(folded)) != len(folded):
        errors.append("zwei Optionen sind nach der Faltung gleich")
    if any(not c.strip() for c in choices):
        errors.append("leere Option")
    # Eine Option, die selbst wie ein Buchstabe aussieht („A"), ließe sich
    # nicht von einer Buchstabenantwort unterscheiden.
    if any(_LEADING_LETTER.fullmatch(c.strip()) for c in choices):
        errors.append("eine Option sieht aus wie ein Lösungsbuchstabe")

    # Das Thema steht auch vorn in der ID („reisen-…“). Zwei Stellen für
    # dieselbe Angabe laufen sonst auseinander, und die Auswertung nach Thema
    # zählte eine Frage unter einem anderen Thema, als ihre ID sagt.
    if not item["id"].startswith(f"{item['topic']}-"):
        errors.append(f"ID beginnt nicht mit dem Thema {item['topic']!r}")
    if "source" in item and not str(item["source"]).startswith("https://"):
        errors.append("Quelle ist keine https-Adresse")

    if TIME_RELATIVE.search(item["question"]):
        errors.append(
            "Frage ist zeitrelativ formuliert (aktuell/derzeit/nächste); "
            "solche Fragen veralten still"
        )
    if fold(item["answer"]) in fold(item["question"]):
        errors.append("die richtige Antwort steht schon in der Frage")
    return errors
