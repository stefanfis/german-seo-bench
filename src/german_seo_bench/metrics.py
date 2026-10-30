"""Metriken, die mehr als eine Aufgabe braucht.

Mehrere Aufgaben schlüsseln nach Stufen auf. Eine Kopie je Aufgabe wäre der
sichere Weg, leicht verschiedene Fassungen derselben Hülle zu bekommen — und
damit Metriknamen, die sich unterscheiden, ohne dass es jemand beabsichtigt hat.
"""

import statistics

from inspect_ai.scorer import Metric, SampleScore, grouped, mean, metric


def profile(group: str) -> Metric:
    """`grouped(mean(), …)` unter einem eigenen Registry-Namen.

    Die Hülle ist kein Zierrat, sondern nötig: Inspect entdoppelt die Metrikliste je Score-Schlüssel über den
    Registry-Namen (`resolve_glob_metric_keys` in `_eval/task/results.py`).
    Mehrere Aufrufe von `grouped(...)` heißen alle "grouped" — alle bis auf den
    ersten fallen still heraus, und die Metriktabelle sieht trotzdem
    vollständig aus.

    Im Log heißt die Spalte dann `german_seo_bench/by_<group>_<wert>`: Inspect
    setzt den vollen Registry-Namen ein und lässt das Paketpräfix nur für seine eigenen
    Metriken weg. Wer die Spalten auswertet, schneidet ein Paketpräfix ab,
    statt auf `by_` am Zeilenanfang zu prüfen (siehe `tests/conftest.py`).

    `all=False`, weil das Gesamtmittel schon als `score/mean` daneben steht —
    zweimal dieselbe Zahl unter zwei Namen liest sich wie zwei Messungen.
    """

    @metric(name=f"by_{group}")
    def factory() -> Metric:
        return grouped(mean(), group, all=False, name_template="{group_name}")

    return factory()


# ------------------------------------------------------------ Judge-Urteile
#
# Ein Judge urteilt „brauchbar“ oder „unbrauchbar“ (`checks/judge.py`). Im
# Score steht das als Anteil der Urteile „unbrauchbar“: 1.0 oder 0.0 je
# Epoche, nach der Reduktion über `--epochs` ein Anteil dazwischen.
#
# Kommt aus einer Antwort kein eindeutiges Urteil heraus, steht dort
# **NO_VERDICT = −1**, ausdrücklich ein Wert außerhalb von 0 bis 1. `None` wäre
# die naheliegende Wahl, wird aber von Inspect zu 0.0 umgewandelt, bevor eine
# Metrik den Wert sieht. Hier hieße 0.0 „brauchbar“: Jedes fehlende Urteil
# würde still als Freispruch zählen.

NO_VERDICT = -1.0


def _pairs(scores: list[SampleScore], reference: str) -> list[tuple[float, float]]:
    """(Anteil unbrauchbar, menschliche Klasse) je Datensatz mit Urteil."""
    return [
        (s.score.as_float(), float((s.sample_metadata or {})[reference]))
        for s in scores
        if s.score.as_float() != NO_VERDICT
    ]


# ------------------------------------------------- „Schlecht erkannt“ (Leitzahl)
#
# Worauf es bei einem Titel-Judge ankommt: Ein Titel, den der
# Mensch als unbrauchbar einstuft, muss auch der Judge als unbrauchbar
# erkennen. Ein brauchbarer Titel darf nicht durchfallen.
#
#   Mensch   1 = unbrauchbar, 2 = vielleicht (Grauzone, zählt nicht),
#            3 = brauchbar
#   Judge    unbrauchbar, wenn die Mehrheit seiner Urteile es sagt
#            (Anteil > 0.5)
#
# Bei einer Epoche ist das genau sein eines Urteil. Bei drei Epochen entscheidet
# die Mehrheit. Ein Gleichstand (bei gerader Zahl gültiger Urteile) gilt nicht
# als „unbrauchbar“: Verworfen wird ein Titel nur, wenn der Judge sich
# mehrheitlich dafür ausspricht.

BAD_HUMAN = 1
GOOD_HUMAN_MIN = 3
UNUSABLE_ABOVE = 0.5


def _bad_recall(pairs: list[tuple[float, float]]) -> float:
    flagged = [j > UNUSABLE_ABOVE for j, h in pairs if h == BAD_HUMAN]
    return statistics.fmean(flagged) if flagged else float("nan")


def _false_alarm(pairs: list[tuple[float, float]]) -> float:
    flagged = [j > UNUSABLE_ABOVE for j, h in pairs if h >= GOOD_HUMAN_MIN]
    return statistics.fmean(flagged) if flagged else float("nan")


@metric(name="bad_recall")
def bad_recall(reference: str = "human_score") -> Metric:
    """Fangquote: Anteil der vom Menschen als unbrauchbar eingestuften Titel, die der Judge erkennt."""

    def compute(scores: list[SampleScore]) -> float:
        return _bad_recall(_pairs(scores, reference))

    return compute


@metric(name="false_alarm")
def false_alarm(reference: str = "human_score") -> Metric:
    """Anteil der vom Menschen als brauchbar eingestuften Titel, die der Judge verwirft."""

    def compute(scores: list[SampleScore]) -> float:
        return _false_alarm(_pairs(scores, reference))

    return compute


@metric(name="balanced_bad")
def balanced_bad(reference: str = "human_score") -> Metric:
    """Die Leitzahl: (Fangquote + (1 − Fehlalarm)) / 2.

    Ausbalanciert, damit sich ein Judge weder mit Milde noch mit Strenge eine
    gute Zahl erkaufen kann. Wer nie „unbrauchbar“ sagt, hat Fangquote 0 und
    Fehlalarm 0, also 0.5. Wer immer „unbrauchbar“ sagt, hat 1 und 1, also
    ebenfalls 0.5. Erst wer die unbrauchbaren Titel findet und die brauchbaren
    verschont, kommt Richtung 1.0. Fehlt eine der beiden Gruppen im Datensatz,
    ist die Zahl nicht definiert (NaN).
    """

    def compute(scores: list[SampleScore]) -> float:
        pairs = _pairs(scores, reference)
        return (_bad_recall(pairs) + 1 - _false_alarm(pairs)) / 2

    return compute


@metric(name="unusable_rate")
def unusable_rate() -> Metric:
    """Anteil der Titel, die der Judge verwirft, unabhängig vom Menschen.

    Die Zahl, die der Judge später über M01-Läufe liefert. Hier steht sie, um
    sie mit dem menschlichen Anteil vergleichen zu können: Ein Judge, der
    deutlich mehr oder weniger verwirft als der Mensch, ist zu streng oder zu
    milde, auch bei guter Leitzahl.
    """

    def compute(scores: list[SampleScore]) -> float:
        judged = [s.score.as_float() for s in scores if s.score.as_float() != NO_VERDICT]
        return statistics.fmean(j > UNUSABLE_ABOVE for j in judged) if judged else float("nan")

    return compute
