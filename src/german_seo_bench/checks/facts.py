"""Prüfungen für die Fakten-Extraktion — ohne Inspect, einzeln testbar.

Gemessen wird nicht "versteht das Modell den Text", sondern ob es die richtigen
Fakten **trennt**: den echten Preis vom Streichpreis, von Versandkosten, von
der Sternebewertung und vom Gewicht in Gramm; die deutsche Preisnotation vom
Zahlenwert; die Artikelnummer von ihrem Präfix; eine fehlende Angabe von einer
erfundenen.

Jedes der sieben Felder ist eine eigene Prüfung mit eigener Vergleichsregel —
und die Regel je Feld ist nicht Geschmack, sondern folgt daraus, was das Feld
ist:

    word    `name`, `material`   Enthaltensein als Wort. "Pfanne TitanCook"
                                 enthält den Modellnamen, "rostfreier Stahl"
                                 das Material. Exakter Abgleich wäre hier ein
                                 Referenz-Artefakt, kein Modellfehler.
    exact   `brand`, `color`,     zeichengleich nach `strip()`, also mit
            `sku`                 Groß-/Kleinschreibung. Streng, und bewusst
                                 so gewollt — siehe unten.
    number  `price_eur`          Zahlenwert mit einem Cent Toleranz.
    bool    `in_stock`           echtes true/false, nicht "ja" als Zeichenkette.

**Warum `exact` streng bleibt, obwohl es hart ist.** Ein Modell, das
"anthrazit" statt "Anthrazit" schreibt, hat die Farbe gefunden und verliert
trotzdem das Feld. Die naheliegende Milderung wäre, gefaltet zu vergleichen.
Sie ist hier NICHT eingebaut: Eine Lockerung mit unbekannter Wirkung ist keine
Verbesserung, sondern eine Änderung, die man später nicht mehr von der
Modellleistung trennen kann. Ob Ausfälle bei `brand` von der Schreibweise
kommen oder davon, dass ein Hersteller erfunden wurde, wo keiner genannt war,
lässt sich aus den Logs beantworten — jede Antwort steht dort im Wortlaut.

Ein fehlender Schlüssel kostet sein Feld, nicht den ganzen Datensatz.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from german_seo_bench.checks.json_answer import candidates, first_json_value
from german_seo_bench.checks.text import fold, word_hit

# Reihenfolge ist Teil des Vertrags: Sie bestimmt, wie die Felder im Prompt
# stehen und in welcher Reihenfolge die Spalten berichtet werden. Der Wert ist
# die Vergleichsregel.
FIELDS: dict[str, str] = {
    "name": "word",
    "brand": "exact",
    "price_eur": "number",
    "color": "exact",
    "material": "word",
    "sku": "exact",
    "in_stock": "bool",
}

# Felder, die im Datensatz `null` sein dürfen, weil der Produkttext die Angabe
# nicht macht. "Nicht da" ist eine Antwort und wird als solche gewertet — sie
# ist sogar die schwierigere, weil ein Modell hier nichts abschreiben kann,
# sondern eine Lücke erkennen muss.
NULLABLE = frozenset({"brand", "color", "material"})

# Preistoleranz in Euro. Ein Cent, damit 199.95 und 199.9500001 dasselbe sind —
# nicht mehr, denn 199.99 ist ein anderer Preis.
PRICE_TOLERANCE = 0.011


def as_number(value: Any) -> float | None:
    """Zahlenwert eines Feldes, oder None, wenn es keiner ist.

    Eine Zeichenkette wird angenommen, wenn sie eine Zahl in
    Programmiernotation enthält ("199.95"). Das ist Absicht:
    Den JSON-Typ zu verfehlen ist eine andere Schwäche als den Preis zu
    verfehlen, und gemessen werden soll die zweite.

    Deutsche Notation wird NICHT angenommen — "1.299,00" bleibt falsch. Genau
    ihre Umrechnung ist die Aufgabe; sie durchzulassen hieße, die Prüfung
    abzuschaffen.

    `bool` wird ausgeschlossen, obwohl Python es als `int` führt. Sonst würde
    ein Modell, das `true` in das Preisfeld schreibt, mit 1.00 Euro gewertet.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.strip().replace(" ", ""))
    except ValueError:
        return None


_NUMBER = re.compile(r"\d[\d.,]*")


def numbers(text: str) -> list[float]:
    """Alle Zahlen aus einem Fließtext, deutsche und englische Notation.

    Für die Prüfung des Datensatzes, nicht der Antwort: Steht der Preis aus
    der Referenz (und jede Preisfalle) wirklich im Produkttext? Der Text
    schreibt deutsch ("1.299,00 €"), die Referenz als Zahl.

    '1.299,00' -> 1299.0, '1,5' -> 1.5, '2081' -> 2081.0. Punkte gelten nur
    dann als Tausendertrenner, wenn sie sauber in Dreiergruppen stehen — sonst
    wäre '3.9' fälschlich 39.
    """
    found: list[float] = []
    for raw in _NUMBER.findall(text):
        raw = raw.rstrip(".,")
        if not raw:
            continue
        if "," in raw and "." in raw:
            candidate = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            candidate = raw.replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            candidate = raw.replace(".", "")
        else:
            candidate = raw
        try:
            found.append(float(candidate))
        except ValueError:
            continue
    return found


def is_empty(value: Any) -> bool:
    """Gilt der Wert als "keine Angabe"?

    `null` ist die verlangte Form; eine leere Zeichenkette zählt mit, weil sie
    dasselbe aussagt. Nicht mitzählen darf "null" als WORT oder "n/a" — das
    wäre eine Angabe über die Lücke statt einer Lücke, und der Unterschied ist
    für ein nachgelagertes Skript erheblich.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def matches(mode: str, want: Any, got: Any) -> bool:
    """Stimmt ein einzelnes Feld? `mode` ist die Regel aus `FIELDS`."""
    if want is None:
        return is_empty(got)
    if is_empty(got):
        return False

    if mode == "number":
        value = as_number(got)
        return value is not None and abs(value - float(want)) < PRICE_TOLERANCE
    if mode == "bool":
        # Streng: `isinstance` vor Gleichheit. Ohne die Typprüfung wäre 1 == True
        # und 0 == False, und "in_stock": 1 ginge als Treffer durch.
        return isinstance(got, bool) and got == bool(want)
    if not isinstance(got, str):
        return False
    if mode == "word":
        return word_hit(fold(got), want)
    if mode == "exact":
        # Nur Leerraum am Rand fällt weg. `fold` wäre hier auch sachlich falsch:
        # Es macht aus "NP-2255-A" ein "np 2255 a" und würde "np2255a" als
        # dieselbe Artikelnummer akzeptieren.
        return got.strip() == str(want)
    raise ValueError(f"unbekannte Vergleichsregel {mode!r}")


# --------------------------------------------------------- JSON aus der Antwort

def extract_json_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Das erste vollständige JSON-Objekt aus einer Modellantwort.

    Rückgabe: (Objekt, Fehlermeldung). Genau eines von beiden ist None.

    Die Toleranz ist nötig, weil kleine Modelle das Objekt gern verpacken: in
    einen ```json-Block, hinter eine Einleitung ("Hier sind die Fakten:"), oder
    in ein einelementiges Array. Nichts davon ist ein Extraktionsfehler, und alles
    davon würde als einer gezählt, wenn man streng `json.loads` aufriefe.

    Was NICHT toleriert wird: ein Array mit mehreren Objekten. Dann hat das
    Modell nicht verpackt, sondern etwas anderes getan als verlangt, und welches
    Objekt gemeint war, ist geraten.
    """
    if not text.strip():
        return None, "leere Antwort"

    for candidate in candidates(text):
        value, error = first_json_value(candidate)
        if value is None:
            continue
        if isinstance(value, dict):
            return value, None
        if isinstance(value, list):
            objects = [item for item in value if isinstance(item, dict)]
            if len(objects) == 1:
                return objects[0], None
            return None, (
                f"JSON-Array mit {len(objects)} Objekten statt eines Objekts"
            )
        return None, f"JSON ist {type(value).__name__}, kein Objekt"
    return None, error or "kein JSON in der Antwort"


# ------------------------------------------------------------------- Wertung


@dataclass(frozen=True)
class FactsVerdict:
    """Das Urteil über eine Antwort, aufgeschlüsselt nach Feld.

    `parsed` und `complete` stehen daneben, weil sie etwas anderes messen als
    die Felder: Ein Modell, das kein JSON liefert, hat sieben Nullen — genau
    wie eines, das den Text nicht lesen kann. Ohne die zwei Spalten wären die
    beiden Fälle in der Tabelle nicht zu unterscheiden, und die Diagnose
    ("Schema-Problem" gegen "Leseproblem") entscheidet darüber, ob ein
    Post-Processing-Schritt hilft oder nicht.
    """

    fields: dict[str, bool]
    parsed: bool
    complete: bool
    reason: str | None = None
    missing: tuple[str, ...] = ()
    unwrapped: bool = False
    price_hint: str | None = None
    wrong: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Alles oder nichts: 1, wenn ALLE sieben Felder stimmen, sonst 0.

        Der Anteil richtiger Felder wäre aus zwei Gründen keine brauchbare
        Leitzahl. Erstens der Sockel: Einige Felder trifft fast jedes Modell,
        das gültiges JSON liefert — der Anteil startet damit weit über null.
        Zweitens der Anwendungsfall: Ein Produktdatensatz mit sechs von sieben
        richtigen Feldern geht nicht zu sechs Siebteln in den Feed. Er muss
        angefasst werden, genau wie einer mit null richtigen Feldern.

        Welches Faktum wie schwer wiegt, bleibt damit ungesagt — das hängt am
        Anwendungsfall und nicht am Modell. Wer es wissen will, liest die
        sieben Feldspalten daneben; sie sind Trefferquoten.
        """
        return float(all(self.fields.values())) if self.fields else 0.0


def grade_facts(
    truth: dict[str, Any], completion: str, price_distractors: list[dict] | None = None
) -> FactsVerdict:
    """Die vollständige Wertung einer Antwort gegen die Referenzfakten."""
    got, error = extract_json_object(completion)
    if got is None:
        return FactsVerdict(
            fields={name: False for name in FIELDS},
            parsed=False,
            complete=False,
            reason=error,
        )

    missing = tuple(name for name in FIELDS if name not in got)
    results = {
        name: name in got and matches(mode, truth[name], got[name])
        for name, mode in FIELDS.items()
    }
    wrong = {name: got.get(name) for name, ok in results.items() if not ok}

    failed = [name for name, ok in results.items() if not ok]
    reason = None if not failed else "falsch: " + ", ".join(failed)
    if missing:
        reason = f"{reason} (fehlende Schlüssel: {', '.join(missing)})"

    return FactsVerdict(
        fields=results,
        parsed=True,
        complete=not missing,
        reason=reason,
        missing=missing,
        price_hint=None
        if results["price_eur"]
        else price_hint(got.get("price_eur"), price_distractors),
        wrong=wrong,
    )


def price_hint(value: Any, distractors: list[dict] | None) -> str | None:
    """Welche der Fallen hat das Modell genommen?

    Für die Wertung macht das keinen Unterschied — ein falscher Preis ist ein
    falscher Preis. Für die Deutung macht es den ganzen: "Streichpreis genommen"
    heißt, das Modell hat die Zahl gefunden und die Regel verfehlt;
    "Gewicht genommen" heißt, es hat nicht verstanden, worum es geht; und eine
    Zahl, die im Text gar nicht vorkommt, heißt erfunden.

    Dasselbe Muster wie `forbidden_echoed` in M01: Die Diagnose steht neben der
    Wertung, nicht in ihr.
    """
    number = as_number(value)
    if number is None:
        return None
    for distractor in distractors or []:
        if abs(number - float(distractor["value"])) < PRICE_TOLERANCE:
            return str(distractor["kind"])
    return None
