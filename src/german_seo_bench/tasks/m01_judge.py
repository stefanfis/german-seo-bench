"""M01-Judge — wie gut stimmt ein Judge-Modell mit menschlichen Urteilen über Titel überein?

Gemessen wird hier nicht ein Titelschreiber, sondern ein **Richter**. Der
Datensatz besteht aus Seitentiteln aus M01-Läufen verschiedener Modelle, jeder von einem
Menschen eingestuft: Würde ein deutscher Muttersprachler den Titel so
schreiben, sprachlich und inhaltlich? Das Modell unter `--model` bekommt
**wörtlich denselben** Prompt wie der Judge für M01-Läufe (`checks/judge.py`)
und antwortet „brauchbar“ oder „unbrauchbar“. Sein Urteil wird mit dem
menschlichen verglichen.

    uv run inspect eval german_seo_bench/m01_judge --model local/gemma4-31b --epochs 3
    uv run inspect eval german_seo_bench/m01_judge --model anthropic/claude-opus-5-5

Daraus folgt, ob man einem Modell als Judge für M01-Läufe trauen kann
(`python -m german_seo_bench.judge`). Derselbe Befehl, einmal mit einem lokalen
und einmal mit einem Frontier-Modell, beantwortet auch die Frage, ob ein lokales
Modell als Richter taugt.

**Leitzahl ist „schlecht erkannt“ (`balanced_bad`).** Einen Titel, den der
Mensch als unbrauchbar einstuft, muss der Judge verwerfen. Einen brauchbaren
darf er nicht verwerfen. Die Leitzahl ist (Fangquote + (1 − Fehlalarm)) / 2;
„vielleicht“ ist Grauzone und zählt nicht. Daneben stehen Fangquote
(`bad_recall`), Fehlalarm (`false_alarm`), der Anteil verworfener Titel
insgesamt (`unusable_rate`) und der Anteil der Antworten, aus denen überhaupt
ein Urteil zu lesen war.

Klassen statt einer Skala: Gebraucht wird nur die Grenze zwischen
„unbrauchbar“ und dem Rest. Eine Rangkorrelation über eine Skala bestrafte jede
Verwechslung zwischen 4 und 5 genauso wie eine übersehene 1 (siehe
`checks/judge.py`).

**Die Datei.** `data/m01_judge.csv` mit genau zwei Spalten, `title,human_score`.
`human_score` ist die menschliche Klasse als Ziffer, damit sich schnell
bewerten lässt: 1 = unbrauchbar, 2 = vielleicht, 3 = brauchbar. Die Datei
entsteht mit `python -m german_seo_bench.export_titles` aus den M01-Logs und
wird von Hand bewertet. Zeilen ohne Bewertung sind noch nicht bewertet und gehören nicht
zum Datensatz. Ist gar keine Zeile bewertet, lässt sich die Aufgabe nicht
starten, und die Meldung sagt, warum. Ein Wert außerhalb von 1 bis 3 oder ein
doppelter Titel ist dagegen ein Fehler der Datei, und die Aufgabe bricht ab,
statt ihn stillschweigend zu überspringen. Mit `-T data=…` lässt sich eine
andere Datei angeben, etwa die, an der man gerade bewertet.
"""

import csv
import hashlib
from importlib import resources
from pathlib import Path

from inspect_ai import Epochs, Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import HeadlineMetric
from inspect_ai.scorer import Score, ScoreReducer, Scorer, Target, mean, score_reducer, scorer
from inspect_ai.solver import TaskState, generate

from german_seo_bench.checks.judge import UNUSABLE, USABLE, judge_prompt, parse_verdict
from german_seo_bench.config import run_config
from german_seo_bench.metrics import (
    BAD_HUMAN,
    NO_VERDICT,
    bad_recall,
    balanced_bad,
    false_alarm,
    unusable_rate,
)
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.server import record_endpoint, record_server

DATA = "m01_judge.csv"
COLUMNS = ["title", "human_score"]

# Die menschlichen Klassen, wie sie in der CSV stehen.
HUMAN_CLASSES = {"1": "unbrauchbar", "2": "vielleicht", "3": "brauchbar"}


def load_ratings(path: str | None = None) -> MemoryDataset:
    """Die bewerteten Titel als Datensatz.

    Die Id ist ein Hash des Titels und nicht die Zeilennummer. Wer die Datei
    sortiert oder neue Zeilen einfügt, behält so dieselben Ids, und zwei Läufe
    lassen sich Datensatz für Datensatz vergleichen.
    """
    if path is None:
        text = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
        location = DATA
    else:
        text = Path(path).read_text(encoding="utf-8")
        location = path

    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames != COLUMNS:
        raise ValueError(f"{location}: Spalten {reader.fieldnames}, erwartet {COLUMNS}")

    samples: list[Sample] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        title = (row["title"] or "").strip()
        raw = (row["human_score"] or "").strip()
        if not raw:
            continue  # noch nicht bewertet
        if raw not in HUMAN_CLASSES:
            raise ValueError(
                f"{location}, Zeile {line}: human_score {raw!r} ist keine Klasse "
                "(1 = unbrauchbar, 2 = vielleicht, 3 = brauchbar)"
            )
        if title in seen:
            raise ValueError(f"{location}, Zeile {line}: Titel doppelt: {title!r}")
        seen.add(title)
        human = int(raw)
        samples.append(
            Sample(
                id=hashlib.sha1(title.encode("utf-8")).hexdigest()[:12],
                input=judge_prompt(title),
                metadata={
                    "title": title,
                    "human_score": human,
                    # Für den Oracle-Lauf. „vielleicht“ wird als brauchbar
                    # beantwortet; die Grauzone zählt in der Leitzahl ohnehin
                    # nicht.
                    "oracle": UNUSABLE if human == BAD_HUMAN else USABLE,
                },
            )
        )
    if not samples:
        # Inspect lehnt einen leeren Datensatz ohnehin ab, aber mit „dataset is
        # empty“. Hier steht, was zu tun ist.
        raise ValueError(
            f"keine bewerteten Titel in {location} — erst bewerten "
            "(python -m german_seo_bench.export_titles) oder -T data=… angeben"
        )
    return MemoryDataset(samples=samples, name="m01_judge", location=location)


@scorer(
    metrics={
        "verdict": [balanced_bad(), bad_recall(), false_alarm(), unusable_rate()],
        "parsed": [mean()],
    }
)
def calibration_scorer() -> Scorer:
    """Liest das Urteil aus und legt es neben die menschliche Klasse.

    `verdict` ist 1.0 für „unbrauchbar“, 0.0 für „brauchbar“ und
    `NO_VERDICT` (−1), wenn kein eindeutiges Urteil zu lesen war. Die Metriken
    lassen −1 aus (siehe metrics.py).
    """

    async def score(state: TaskState, target: Target) -> Score:
        verdict = parse_verdict(state.output.completion)
        human = HUMAN_CLASSES[str(state.metadata["human_score"])]
        return Score(
            value={
                "verdict": NO_VERDICT if verdict is None else float(verdict == UNUSABLE),
                "parsed": int(verdict is not None),
            },
            answer=state.output.completion,
            explanation=(
                "kein eindeutiges Urteil in der Antwort"
                if verdict is None
                else f"Judge {verdict}, Mensch {human}"
            ),
        )

    return score


@score_reducer(name="verdict_share")
def verdict_share() -> ScoreReducer:
    """Fasst die Epochen eines Titels zum Anteil der Urteile „unbrauchbar“ zusammen.

    Mit `--epochs 3` urteilt der Judge dreimal über denselben Titel, und Inspect
    reduziert die drei Scores vor den Metriken auf einen. Gezählt werden nur
    lesbare Urteile; ein fehlendes Urteil ist weder Freispruch noch Verwerfung.
    Aus „unbrauchbar, unbrauchbar, (unlesbar)“ wird 1.0, nicht 0.67 wie beim
    Standard-Reducer, der −1 mitmitteln würde. Gibt es kein einziges lesbares
    Urteil, bleibt es bei `NO_VERDICT`. `parsed` wird als Anteil gemittelt.
    """

    def reduce(scores: list[Score]) -> Score:
        verdicts = [s.value["verdict"] for s in scores if s.value["verdict"] != NO_VERDICT]
        return Score(
            value={
                "verdict": sum(verdicts) / len(verdicts) if verdicts else NO_VERDICT,
                "parsed": sum(s.value["parsed"] for s in scores) / len(scores),
            }
        )

    return reduce


# Aufgabenversion, eine ganze Zahl wie bei jeder Aufgabe. Steigt, sobald sich
# der Judge-Prompt ändert (`JUDGE_VERSION` in `checks/judge.py`) oder die
# Rechnung hier. Welche Titel bewertet wurden, gehört nicht dazu: Das vergleicht
# `report.py` ohnehin über die Datensätze eines Laufs.
VERSION = 1


@task
def m01_judge(oracle: bool = False, data: str | None = None) -> Task:
    """Kalibrierung des M01-Titel-Judges gegen menschliche Bewertungen.

    Args:
        oracle: Statt eines Modells die menschliche Klasse als Urteil
            einsetzen. Muss die Leitzahl 1.00 ergeben.
        data: Pfad zu einer eigenen CSV (`title,human_score`) statt der
            mitgelieferten.
    """
    return Task(
        dataset=load_ratings(data),
        solver=oracle_solver()
        if oracle
        else [record_endpoint(), generate(), record_server()],
        scorer=calibration_scorer(),
        # Ein Durchgang wie bei jeder Aufgabe; `--epochs` auf der
        # Kommandozeile erhöht die Zahl und behält den Reducer.
        epochs=Epochs(1, verdict_share()),
        config=run_config(),
        version=VERSION,
        display_name="M01-Judge — Titel-Judge gegen Menschen",
        headline_metric=HeadlineMetric(score="verdict", metric="german_seo_bench/balanced_bad"),
    )
