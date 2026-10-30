"""M03 — Fakten-Extraktion aus deutschen Produkttexten, deterministisch bewertet.

Das Szenario ist das häufigste Datenprojekt im SEO-Alltag: Aus Fließtext soll
strukturiertes JSON werden — für ein Feed, für Schema.org-Auszeichnung, für
einen Abgleich gegen das PIM. Ein Modell, das das zuverlässig kann, ersetzt
einen Haufen Regex; eines, das es fast kann, erzeugt stille Fehler in einer
Datenbank.

Gemessen wird nicht "versteht das Modell den Text", sondern ob es die richtigen
Fakten **trennt**. Jeder Produkttext trägt seine Fallen selbst:

    Streichpreis      "Statt 249,00 € jetzt nur 199,95 €"
    Versandkosten     "29,90 €. Versand 4,99 €."
    Sternebewertung   "4,6 Sterne bei 312 Bewertungen"
    Gewicht in Gramm  "Nur 1.199,00 g leicht" — neben einem Preis von 1.049,00 €
    Garantie          "5 Jahre Garantie"
    deutsche Notation "1.299,00" ist 1299.00, nicht 1.299
    Präfix            "Art.-Nr. SW-4471" -> nur "SW-4471"
    fehlende Angabe   kein Hersteller genannt -> null, nicht geraten
    Grundpreis        "5,97 € (100 ml = 1,99 €)"
    Ab-Preis          "alle Größen ab 449,00 €" neben dem Preis der gezeigten
    zweites Produkt   "Passendes Zubehör: Ersatzkartusche, 12,90 €"
    EAN neben SKU     "EAN 4012345678901, Art.-Nr. KS-7741"
    Staffelpreis      "Einzelpreis 11,50 €, ab 10 Stück 8,90 €"
    Steueranteil      "299,00 € inkl. 19 % MwSt. (Steueranteil 47,74 €)"
    Farbe ist Material "Farbe Titan, Gehäuse aus gebürstetem Aluminium"
    Lager ohne Stichwort "Restposten, solange der Vorrat reicht"

Die Gegenprobe zur Gewichtsfalle steht im Datensatz `aerosafe`: Dort IST
1.199,00 € der Preis. Ein Modell, das sich die Regel "vierstellig heißt Gramm"
gebaut hat, fällt genau dort durch.

Die Datensätze tragen eine **Stufe** (1 bis 5, je sechs). Sie ist
**nicht kalibriert**: Das Einzelergebnis eines Datensatzes schwankt bei
kleinen Modellen schon bei kleinen Prompt-Änderungen stark, und eine Stufe
setzt genau die Stabilität voraus, die dort fehlt. `by_level_…` ist deshalb
**kein Schwierigkeitsprofil**, sondern eine Gruppierung ohne belegte
Bedeutung. Neu geschnitten wird mit `python -m german_seo_bench.itemcheck`,
sobald mindestens drei Modelle gelaufen sind — die Zusicherung dort verweigert
unterhalb davon bewusst jedes Urteil.

Gewertet wird über `checks/facts.py`: sieben Felder, je Feld eine eigene
Vergleichsregel, kein Judge, kein Urteil.

**Bauentscheidungen — jede mit Grund:**

*Ein Produkt je Anfrage, kein Stapel.* Im Stapel beeinflussen sich die
Datensätze gegenseitig, und ein Produkt kann kippen, nur weil ein anderes
daneben steht. Ein Array mit der falschen Länge verwürfe außerdem mehrere
Produkte auf einen Schlag.

*Englische Feldnamen.* `name`, `price_eur`, `in_stock`: Wer aus einem
Produkttext JSON-LD baut, schreibt `brand`, `sku`, `color` — so heißen die
Felder bei Schema.org. Der deutsche Text bleibt deutsch; nur das Zielschema ist
englisch, und genau diese Mischung liegt in der Praxis vor.

*Ein fehlender Schlüssel kostet sein Feld, nicht den Datensatz.* Ein Modell,
das sechs Felder richtig hat und `color` weglässt, hätte sonst null. Der
fehlende Schlüssel zählt als falsches Feld, und die Spalte `complete` sagt, wie
oft der Vertrag überhaupt eingehalten wurde.

*Kein Hinweis auf die Fallen im Prompt.* Ein Satz wie „Streichpreis,
Versandkosten, Sternebewertung … sind NICHT der Preis“ gäbe die schwierige
Hälfte weg, und `price_eur` mäße Anweisungsbefolgung statt
Unterscheidungsvermögen. Der Prompt sagt nur, WAS gesucht wird.

*`score` ist alles oder nichts.* 1, wenn alle sieben Felder stimmen, sonst 0.
Der Anteil richtiger Felder hätte einen hohen Sockel, weil einige Felder fast
jedes Modell trifft, das gültiges JSON liefert. Und er entspräche nicht dem
Anwendungsfall: Ein Datensatz mit sechs von sieben richtigen Feldern geht nicht
zu sechs Siebteln in den Feed.

Die sieben Feldspalten stehen daneben und sind der diagnostische
Teil — sie sagen, WELCHES Faktum fehlt. `parsed` und `complete` ebenso: Ein
Modell ohne JSON hat sieben Nullen, genau wie eines, das den Text nicht lesen
kann, und nur diese beiden Spalten unterscheiden die Fälle.
"""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import HeadlineMetric
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState, generate, system_message

from german_seo_bench.checks.facts import FIELDS, grade_facts
from german_seo_bench.config import run_config
from german_seo_bench.dataset import dataset_version, load_dataset
from german_seo_bench.metrics import profile
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.server import record_endpoint, record_server

DATA = "m03_facts.json"

SYSTEM = "Du bist eine Extraktions-Engine. Du antwortest ausschließlich mit JSON."

# Die Feldbeschreibung, die das Modell sieht. Reihenfolge und Bestand müssen zu
# `FIELDS` passen — `test_vertrag_und_wertung_passen_zusammen` sichert das zu.
# Liefen die beiden auseinander, verlangte der Prompt etwas anderes, als die
# Wertung prüft, und die Differenz sähe wie Modellversagen aus.
RULES = """Felder:
  name        string, der Eigen- oder Modellname des Produkts (ein Wort)
  brand       string oder null — der Hersteller oder Anbieter. Nennt der Text
              nur EINEN Eigennamen, ist das der Produktname und nicht der
              Hersteller; dann ist brand null
  price_eur   Zahl, der tatsächliche Verkaufspreis in Euro, mit Dezimalpunkt,
              ohne Tausendertrennung, ohne Währungssymbol
  color       string oder null
  material    string oder null, der Werkstoff des Produkts selbst — nicht der
              einer Beschichtung, Verpackung oder Zugabe
  sku         string, die Artikelnummer vollständig und unverändert, nur ohne
              das Etikett davor: "Art.-Nr. SW-4471" -> "SW-4471"
  in_stock    true, wenn lieferbar oder auf Lager; false, wenn ausverkauft
              oder nicht verfügbar"""

# Die Formatvorgabe am Ende des Prompts. „Ein einziges JSON-Objekt" allein
# reicht nicht: `{"products": [{...}]}` ist auch eines, und ein Modell, das so
# antwortet, scheitert an allen sieben Schlüsseln, obwohl es richtig extrahiert
# hat. Deshalb steht die oberste Ebene ausdrücklich da. Den Scorer stattdessen
# Hüllen auspacken zu lassen, wäre falsch: Jede Hülle bräuchte eine eigene
# Sonderregel, und das Format wäre keine Anforderung mehr.
ANSWER_FORMAT = (
    f"Antworte mit genau einem JSON-Objekt, das diese {len(FIELDS)} Schlüssel "
    "auf oberster Ebene enthält, nichts sonst."
)

# Die zwei mechanischen Spalten neben den Feldern. Eine Quelle für Reihenfolge
# und Bestand.
FLAGS = ("parsed", "complete")

# Wonach `score` zusätzlich aufgeschlüsselt wird. Ohne das Stufenprofil
# verschwindet Sättigung im Mittel: Was im Gesamtwert wie Können aussieht,
# kann im Profil zeigen, dass nur die unteren Stufen gelöst werden.
GROUPS = ("level",)


def prompt(inputs: dict[str, Any]) -> str:
    """Was das Modell sieht. Bekommt NUR den Eingabe-Teil des Datensatzes.

    **Ohne Hinweis auf die Fallen.** Stünde im Prompt, dass Streichpreis,
    Versandkosten, Sternebewertung, Garantiejahre und Gewichtsangaben NICHT
    der Preis sind, mäße die Aufgabe nur noch, ob ein Modell eine Regel
    anwenden kann, die vollständig im Prompt steht. Der Prompt sagt deshalb
    nur, WAS gesucht wird, nicht wie man es von seinen
    Verwechslungskandidaten trennt. Das Unterscheiden ist die Aufgabe.
    """
    return (
        "Extrahiere aus dem folgenden Produkttext die strukturierten "
        "Produktfakten.\n\n"
        f"{RULES}\n\n"
        f"Produkttext:\n{inputs['text']}\n\n"
        f"{ANSWER_FORMAT}"
    )


@scorer(
    metrics=[
        {
            "score": [mean(), stderr(), *(profile(group) for group in GROUPS)],
            # Je Feld die Trefferquote. Erst sie macht die Zahl diagnostisch:
            # Sie sagt, WELCHES Faktum ein Modell nicht findet. Ohne sie wäre
            # `score` 0.71 nicht von `score` 0.71 zu unterscheiden — einmal
            # "verliert überall ein Feld", einmal "findet den Preis nie".
            **{name: [mean()] for name in FIELDS},
            **{flag: [mean()] for flag in FLAGS},
        }
    ]
)
def facts_scorer() -> Scorer:
    """Die Wertung. Sieht Eingabe und Referenz — anders als `prompt()`.

    Die Logik steht vollständig in `checks/facts.py` und ist dort ohne Inspect
    testbar; hier wird nur zusammengesetzt, was ins Log gehört.
    """

    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata
        verdict = grade_facts(
            meta["facts"], state.output.completion, meta.get("price_distractors")
        )

        # Eine an `max_tokens` abgeschnittene Antwort liefert unvollständiges
        # JSON und scheitert damit an `parsed` — und sähe aus wie ein Modell,
        # das kein JSON schreiben kann. Bei der Extraktion ist das der
        # wahrscheinlichste Abbruchgrund von allen, weil ein Reasoning-Modell
        # über sieben Felder lange nachdenken kann.
        truncated = state.output.stop_reason == "max_tokens"

        explanation = verdict.reason or "alle Felder richtig"
        if truncated:
            explanation = f"bei max_tokens abgeschnitten — {explanation}"
        if verdict.price_hint:
            explanation += f" (Preis: {verdict.price_hint} genommen)"

        return Score(
            value={
                "score": verdict.score,
                **{name: int(ok) for name, ok in verdict.fields.items()},
                "parsed": int(verdict.parsed),
                "complete": int(verdict.complete),
            },
            answer=state.output.completion.strip(),
            explanation=explanation,
            metadata={
                "missing_keys": list(verdict.missing),
                # Die abgelehnten Werte im Wortlaut. Ohne sie müsste man für
                # jede bestrittene Wertung die Antwort im Log von Hand mit der
                # Referenz vergleichen.
                "wrong_values": verdict.wrong,
                "price_hint": verdict.price_hint,
                "truncated": truncated,
            },
        )

    return score


@task
def m03_facts(oracle: bool = False) -> Task:
    """Fakten-Extraktion aus deutschen Produkttexten.

    Args:
        oracle: Statt eines Modells die Musterlösung einsetzen. Muss 1.00
            ergeben — sonst ist die Wertung kaputt und jede Zahl aus dieser
            Aufgabe wertlos.
    """
    return Task(
        dataset=load_dataset(DATA, prompt),
        # `record_endpoint()` steht DAVOR und `record_server()` dahinter.
        # Die Reihenfolge ist in beide Richtungen begründet: Die Zieladresse
        # muss aufgezeichnet sein, bevor etwas scheitern kann — sonst fehlt
        # sie genau im Fehlerfall. Was der Server geladen hat, weiß man
        # umgekehrt erst hinterher: Erst danach hat
        # llama-swap das Modell geladen und `/props` meldet das richtige.
        solver=oracle_solver()
        if oracle
        else [
            record_endpoint(),
            system_message(SYSTEM),
            generate(),
            record_server(),
        ],
        scorer=facts_scorer(),
        config=run_config(),
        version=dataset_version(DATA),
        display_name="M03 — Fakten-Extraktion",
        headline_metric=HeadlineMetric(score="score", metric="mean"),
    )
