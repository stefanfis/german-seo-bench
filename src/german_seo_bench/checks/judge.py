"""Der Titel-Judge: Prompt und Auslesen, eine Quelle für zwei Stellen.

Gebraucht wird beides an zwei Stellen. `python -m german_seo_bench.judge`
bewertet damit die Titel eines fertigen M01-Laufs, und `m01_judge` misst, wie gut ein
Judge-Modell mit menschlichen Bewertungen übereinstimmt. Die Kalibrierung sagt
nur dann etwas über den Judge in M01, wenn beide Stellen **wörtlich denselben**
Prompt stellen und die Antwort **gleich** auslesen. Deshalb gibt es beides nur
hier.

Der Judge beantwortet genau eine Frage: Würde ein deutscher Muttersprachler
diesen Titel so schreiben? Dazu gehören Grammatik und Idiomatik, aber auch, ob
der Titel in sich Sinn ergibt: Ein Titel kann sprachlich sauber und trotzdem
Unsinn sein. Ob er in sich stimmig ist, lässt sich am Titel allein erkennen;
der Seitentext ist dafür nicht nötig.

**Zwei Klassen statt einer Skala.** Gebraucht wird nur eine Entscheidung: Ist
der Titel unbrauchbar? Zwischen Noten wie 3, 4 oder 5 kann schon ein Mensch
nicht sicher unterscheiden, und Judges weichen auf einer Skala gern nach oben
aus. Der Judge antwortet deshalb mit „brauchbar“ oder „unbrauchbar“. Der
Mensch hat eine dritte Klasse, „vielleicht“, die in der Messung als Grauzone
nicht zählt (siehe `balanced_bad` in metrics.py).

**„Kein Titel“ ist unbrauchbar.** Ganze Sätze und Bruchstücke wie „Aber nicht
in der Praxis“ taugen nicht als Überschrift, auch wenn sie sprachlich sauber
sind. Die Zeichenzahl bleibt ausgenommen: Ein Satz ist nicht deshalb kein
Titel, weil er lang ist, sondern weil er eine Aussage macht statt ein Thema zu
nennen.

Länge, Keyword, Thema und Format prüft M01 deterministisch. Ein Judge, der das
zusätzlich bewertet, würde dasselbe zweimal messen, einmal davon unscharf. Der
Prompt nennt diese Punkte deshalb ausdrücklich als nicht zu bewerten.
"""

import re

# Steigt, sobald sich `judge_prompt()` oder `parse_verdict()` ändern.
# `judge.py` schreibt sie in jeden Sidecar-Eintrag; mit ihr steigt auch die
# Aufgabenversion von `m01_judge` (`VERSION` dort). Ein Urteil nach altem Prompt gilt dann nicht mehr als Urteil
# desselben Judges, und `report.py` meldet die Mischung.
JUDGE_VERSION = 1

USABLE = "brauchbar"
UNUSABLE = "unbrauchbar"

# Das Beispiel ist frei erfunden und steht in keinem bewerteten Datensatz.
# Stammte es aus `m01_judge.csv`, sagte der Prompt dem Judge in der
# Kalibrierung die Antwort auf genau diesen Fall vor.
# `test_prompt_beispiel_steht_in_keinem_datensatz` hält das fest.
EXAMPLE_TITLE = "Wintermantel kaufen: Futter, Schnitt und was die Kälte will"

_CLASSES = f"""{USABLE} = so könnte es eine deutsche Redaktion schreiben: natürlich und
    in sich stimmig. Auch ein schlichter oder allgemeiner Titel ist brauchbar.
{UNUSABLE} = ungrammatisch, kein natürliches Deutsch, Wortsalat,
    inhaltlicher Unsinn (Beispiel: „{EXAMPLE_TITLE}“ –
    sprachlich sauber, aber die Kälte will nichts), oder kein Titel: ein
    ganzer Satz statt einer Überschrift, oder ein Fragment, das allein nicht
    als Überschrift taugt"""


def judge_prompt(title: str) -> str:
    """Was das Judge-Modell sieht: nur den Titel, keinen Seitentext.

    Ohne Seitentext, weil die Frage nicht lautet „passt der Titel zum Text“.
    Das deckt M01 mit `keyword` und `meaning` ab. Ob der Titel **in sich**
    Sinn ergibt, ist dagegen gefragt, und das steht im Titel selbst.
    """
    return (
        "Würde ein deutscher Muttersprachler den folgenden Seitentitel so "
        "schreiben?\n\n"
        "Beurteile Sprache und Sinn: Grammatik, natürliche Wortwahl und ob der "
        "Titel in sich stimmig ist. Nicht beurteilen: Zeichenzahl, Suchbegriffe, ob er "
        "zu einem Thema passt, Zeichensetzung als Format.\n\n"
        f"Klassen:\n{_CLASSES}\n\n"
        f"Titel: {title}\n\n"
        f"Antworte nur mit einem Wort: {USABLE} oder {UNUSABLE}."
    )


# Ein Denkblock, den ein Server nicht abgetrennt hat. Inspect trennt
# Reasoning-Inhalte selbst ab, sobald der Server sie als solche meldet. Steht
# der Block aber wörtlich im Text (llama-server ohne `--reasoning-format`),
# erwägt er meist beide Klassen, und das würde das Urteil uneindeutig machen.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# „unbrauchbar“ enthält „brauchbar“; deshalb wird jedes Vorkommen als ganzes
# Wort gelesen, mit optionalem „un“ davor. „nicht brauchbar“ ist dieselbe
# Aussage wie „unbrauchbar“ und wird vorher vereinheitlicht.
_VERDICT = re.compile(r"\b(un)?brauchbar\b", re.IGNORECASE)
_NOT_USABLE = re.compile(r"\bnicht\s+brauchbar\b", re.IGNORECASE)


def parse_verdict(text: str) -> str | None:
    """Das Urteil aus der Antwort, oder `None`, wenn es keines eindeutig gibt.

    Toleriert wird Beiwerk wie „Urteil: brauchbar“, Großschreibung oder eine
    angehängte Begründung, denn auch ein Modell, das die Formatvorgabe nicht
    ganz einhält, hat geurteilt. Nennt die Antwort aber beide Klassen („eher
    brauchbar als unbrauchbar“), bleibt offen, welche gemeint ist, und jede
    Wahl wäre geraten.
    """
    text = _NOT_USABLE.sub(UNUSABLE, _THINK.sub("", text))
    found = {UNUSABLE if m.group(1) else USABLE for m in _VERDICT.finditer(text)}
    return found.pop() if len(found) == 1 else None
