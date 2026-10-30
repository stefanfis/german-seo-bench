"""Sprachprüfung: Ist der Titel Deutsch?

Der Detektor ist harte Abhängigkeit, nicht optional. Ein Rückfall auf eine
reine Wortliste ergäbe zwei Läufe, die gleich heißen, aber verschieden messen.
Fehlt der Detektor, soll der Lauf abbrechen, nicht schwächer messen.

Warum `py3langid` und nicht `lingua`: Gemessen auf 66 echten Modellantworten
und 20 englischen Gegenproben treffen beide dieselben 86 Entscheidungen — null
Abweichungen, beide fehlerfrei. `lingua` kostet dafür 164 MB Download,
`py3langid` 4,4 MB. Bei einem Benchmark, den man per `uvx` ohne Installation
starten können soll, ist der erste Eindruck ein Fortschrittsbalken von
164 MB — für eine Prüfung, die in 15 % der Fälle überhaupt zum Zug kommt.
"""

import re
from functools import lru_cache
from importlib import resources

from py3langid.langid import MODEL_FILE, LanguageIdentifier

# Deutsche Funktionswörter, die es im Englischen nicht gibt. Ein Treffer ist ein
# starkes DE-Signal. Nötig, weil Sprachdetektoren auf kurzen Titeln mit
# Anglizismen ("Golf", "Test", "Tipps") das Top-1-Label verfehlen.
_GERMAN_WORDS = frozenset(
    {
        "und", "für", "mit", "im", "aus", "bei", "gegen", "vor", "von", "auf",
        "zum", "zur", "beim", "der", "die", "das", "den", "dem", "des", "ein",
        "eine", "kaufen", "pflegen", "lernen", "sparen", "erkennen", "reinigen",
        "wechseln", "schützen", "richtig", "besten", "günstig", "anleitung",
        "ratgeber", "vergleich", "pflege", "ursachen", "kosten", "tipps",
        "sicher", "so",
    }
)

# Auf die Sprachen eingeschränkt, die in deutschen SEO-Titeln realistisch
# verwechselt werden können. Ohne die Einschränkung rät der Detektor über 97
# Sprachen und wird auf kurzen Zeichenketten unnötig unsicher.
_LANGUAGES = ["de", "en", "fr", "es", "it", "nl"]


@lru_cache(maxsize=1)
def _detector() -> LanguageIdentifier:
    """Eigene Instanz statt `py3langid.classify`.

    Die Modulfunktionen von py3langid arbeiten auf einem globalen Detektor, und
    `set_languages()` verändert ihn für alle Aufrufer im Prozess. Eine eigene
    Instanz kann niemandem sonst die Einstellung unter den Füßen wegziehen.
    """
    model = resources.files("py3langid").joinpath(MODEL_FILE)
    detector = LanguageIdentifier.from_modelpath(str(model))
    detector.set_languages(_LANGUAGES)
    return detector


def is_german(title: str) -> bool:
    """Drei Stufen: erst harte Signale, dann der Detektor.

    Die Reihenfolge ist wichtig. Ein Detektor stuft "Golf lernen für Anfänger"
    gern als Englisch ein, weil die Wörter kurz sind und der Satz keine
    Grammatik hat. Ein Umlaut oder ein deutsches Funktionswort ist dagegen ein
    Beweis, den kein Wahrscheinlichkeitsmodell überstimmen sollte.

    Stufe 3 entscheidet in etwa 15 % der Fälle — gemessen an echten
    Modellantworten. Sie ist also weder Zierrat noch der Normalfall.

    Kein Konfidenz-Schwellwert: Auf allen gemessenen Fällen, in denen Stufe 3
    zum Zug kam, lag Deutsch mit deutlichem Abstand auf Platz 1. Eine
    kalibrierte Schranke wäre eine Konstante, die bei jeder
    Datensatzerweiterung neu zu rechtfertigen wäre.
    """
    lower = title.lower()
    if any(char in lower for char in "äöüß"):
        return True
    if set(re.findall(r"[a-zäöüß]+", lower)) & _GERMAN_WORDS:
        return True
    language, _ = _detector().classify(title)
    return bool(language == "de")
