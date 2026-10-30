"""`python -m german_seo_bench.report` — rechnet mehrere Läufe zu Tabellen zusammen.

Das Modul **wertet nichts neu und prüft keinen Lauf**. Jede Zahl, die es
verwendet, ist ein Score, den der Scorer der Aufgabe selbst ins Log
geschrieben hat: je Datensatz der Wert unter dem Schlüssel des Leitwerts
(`eval.headline_metric.score`, bei allen Aufgaben `score`). Ob ein Lauf
stimmt — stummer Endpunkt, abgeschnittene Antwort, Formatfehler —, entscheidet
der Scorer der Aufgabe und nur er. Stünde dieselbe Prüfung zusätzlich hier,
gäbe es zwei Stellen, die sich widersprechen können, und die zweite veraltet
lautlos, sobald die erste sich ändert.

Geprüft wird hier deshalb nur, was **erst beim Zusammenfassen** sichtbar wird
und in keinem einzelnen Lauf auffallen kann:

- Läufe derselben Aufgabe mit verschiedener Aufgabenversion. Jeder Lauf für
  sich ist in Ordnung; ihr Mittel ist eine Zahl über zwei verschiedene
  Benchmarks.
- Läufe derselben Aufgabe mit verschiedenen Aufgabenparametern (`-T …`) oder
  einem anderen Ausschnitt des Datensatzes (`--limit`, `--sample-id`). Aus
  demselben Grund: ein Teilausschnitt ist ein gültiger Lauf, aber neben einem
  vollständigen nicht vergleichbar.

Jeder dieser Fälle bricht ab, mit allen betroffenen Dateien. Ausgewertet wird
genau, was ausgewählt wurde; eine Auswahl oder Einordnung trifft das Modul
nicht.

Logs ohne Ergebnis (Fehler, abgebrochen) lassen sich nicht einrechnen, brechen
aber nicht ab: Ein Lauf, der an einem hängenden Server gescheitert ist, sagt
nichts über die übrigen. Sie stehen mit ihrer Ursache unter „Abgebrochene
Läufe“, nie still herausgefallen. Fehlt einem Modell dadurch eine Aufgabe,
zeigt die Tabelle das wie jede fehlende Aufgabe (Gesamt mit *); ein Modell
ganz ohne Ergebnis steht nur dort. Sind alle Läufe der Auswahl ohne Ergebnis,
bricht der Bericht ab und nennt sie.

Gruppiert wird nach (Aufgabe, Modell). Mehrere Läufe derselben Gruppe sind
Wiederholungen, und sie werden **nicht gemittelt**: Je Datensatz zählt der
schlechteste Score über alle Läufe, die Zelle ist das Mittel dieser Werte
über die Datensätze. Ein Modell, das einen Titel in vier von fünf Läufen gut
und im fünften völlig daneben schreibt, ist für den unbeaufsichtigten Einsatz
nicht zu gebrauchen; im Mittel der Läufe fiele dieser Aussetzer kaum auf. Das
Minimum der Laufmittel wäre die naheliegende, aber falsche Alternative: Zwei
Läufe, die an verschiedenen Datensätzen scheitern, ergäben dort 0.5 und nicht
0 — obwohl auf keinen der beiden Datensätze Verlass ist.

Damit ist die Zelle umso strenger, je mehr Läufe in ihr stecken. Verschiedene
Laufzahlen sind trotzdem zulässig; die Zahl steht dann in der Zelle. Bei
Temperatur 0 liefern alle Läufe dasselbe und der schlechteste ist der einzige —
das sieht aus wie Verlässlichkeit und ist keine (`docs/architecture.md`).

Der Gesamtscore eines Modells ist das arithmetische Mittel seiner
Aufgabenwerte — jede Aufgabe zählt gleich, unabhängig von ihrer Zahl an
Datensätzen. Fehlt einem Modell eine
Aufgabe, wird der Gesamtscore über die vorhandenen gerechnet und als
unvollständig gekennzeichnet; eine fehlende Aufgabe als 0 zu zählen, wäre eine
Messung, die nie stattgefunden hat.

Trägt ein Modellname einen Denkmodus (`-think-off`, `-think-high`; bei
Cloud-Modellen aus `reasoning_effort` im Log ergänzt, `modelname.py`), steht
davor eine Zusammenfassung mit einer Zeile je Modell und je Aufgabe einer
Spalte pro Modus, „aus“ oder „an“. Sie legt nur zusammen und rechnet nichts; die Gründe stehen
unter „Denkmodi“ weiter unten.

Jede Ausgabe nennt die Benchmark-Version, also die Paketversion von
german-seo-bench, die Inspect in jedes Log schreibt (`eval.packages`). Sie
sagt, aus welchem Stand die Zahlen stammen. Läufe verschiedener
Benchmark-Versionen werden trotzdem zusammengefasst, solange die
Aufgabenversionen übereinstimmen: Vergleichbar macht zwei Scores die
Aufgabenversion, nicht der Stand des Pakets. Der Bericht nennt dann jede
Benchmark-Version mit der Zahl ihrer Läufe. Daneben steht der Messzeitraum:
die Monate, in denen die eingerechneten Läufe gestartet wurden. Logs, die vor der ersten
Veröffentlichung entstanden sind, tragen `0.1.0`; der Bericht zeigt sie als
`2026.1` (`RENAMED_VERSIONS`).

Mit `--out` stehen in `report.md` und `report.html` hinter der Tabelle die
Laufdaten — Sampling, Laufzeit, Tokens/s, größter Kontext (`runstats.py`) —
und die Einzelergebnisse: jede Antwort mit Score und Begründung aus dem Log,
neben der Musterlösung (`details.py`). Auch dort wird nichts neu gewertet.
Dazu kommen Diagramme: Gesamtscore und Heatmap, je Aufgabe ein
Balkendiagramm und mit `--model-sizes` der Score gegen die Dateigröße.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from inspect_ai.log import EvalLog, read_eval_log

from german_seo_bench.details import Answer, log_answers, render_details, render_html
from german_seo_bench.judge import sidecar_path
from german_seo_bench.modelname import BUDGET_MARK, THINK_OFF, THINK_SUFFIX, model_label
from german_seo_bench.runstats import (
    RunStats,
    StatsCell,
    combine,
    run_stats,
    sampling_by_model,
    wall_seconds,
)

# Wie eine Zelle entsteht: je Datensatz der schlechteste Lauf, dann das Mittel
# über die Datensätze; Gesamt ist das Mittel der Aufgaben. Steht im JSON, damit
# eine Zahl ihre Rechenart mitträgt.
AGGREGATION = "worst_run_per_sample_then_mean"

# Unter diesem Namen steht die Benchmark-Version in `eval.packages`.
PACKAGE = "german-seo-bench"

# Versionen, die im Log anders heißen als im Bericht. Vor der ersten
# Veröffentlichung trug das Paket `0.1.0`; veröffentlicht wurde derselbe
# Benchmark als `2026.1`. Das Log bleibt, wie Inspect es geschrieben hat.
RENAMED_VERSIONS = {"0.1.0": "2026.1"}


class ReportError(Exception):
    """Die Auswahl lässt sich nicht zusammenrechnen. Der Text nennt die Dateien."""


# --------------------------------------------------------------- Einlesen


@dataclass(frozen=True)
class Run:
    """Ein Lauf, reduziert auf das, was die Zusammenfassung braucht."""

    file: str
    task: str
    display_name: str
    version: str
    model: str
    score: float
    stderr: float | None
    created: str
    # Was neben der Version bestimmt, welcher Benchmark gelaufen ist. Als
    # kanonisches JSON, damit zwei Läufe mit gleichem Aufbau gleich vergleichen.
    task_args: str = "{}"
    sample_ids: str = "[]"
    # Nur für den Abgleich mit dem Judge-Sidecar daneben (`judge.py`).
    eval_id: str = ""
    # Als Zeichenkette wie die übrigen Vergleichsfelder. Ein Lauf ohne
    # `--epochs` hat eine Epoche, Inspect schreibt dann aber `None` ins Log.
    epochs: str = "1"
    # Paketversion von german-seo-bench, mit der der Lauf entstand; leer, wenn
    # das Log sie nicht enthält.
    benchmark_version: str = ""


@dataclass(frozen=True)
class Failed:
    """Ein Lauf ohne Ergebnis: nicht in den Tabellen, aber mit Ursache im Bericht."""

    file: str
    task: str
    model: str
    status: str
    # Die Fehlermeldung, so genau wie das Log sie hergibt (`_cause`).
    cause: str
    # Modellaufrufe bis zum Abbruch und Wanduhr des Laufs; None, wenn das Log
    # sie nicht enthält.
    calls: int | None
    seconds: float | None


def select_logs(log_dir: Path, pattern: str = "*.eval", since: datetime | None = None) -> list[Path]:
    """Die Dateien der Auswahl, nach Namen sortiert (und damit nach Startzeit).

    `since` wird an `eval.created` gemessen, nicht am Dateinamen — beides ist
    UTC, aber nur das eine ist ein Feld und kein Namensschema.
    """
    if not log_dir.is_dir():
        raise ReportError(f"Verzeichnis nicht gefunden: {log_dir}")
    files = sorted(p for p in log_dir.glob(pattern) if p.is_file() and p.suffix == ".eval")
    if since is not None:
        # Hier und nicht erst in der Kommandozeile: Wer `build()` direkt mit
        # einem Datum ohne Zeitzone aufruft, bekäme sonst einen TypeError statt
        # einer Auswahl.
        since = _as_utc(since)
        files = [p for p in files if _created(p) >= since]
    if not files:
        raise ReportError(
            f"Keine .eval-Datei in {log_dir} passt zu {pattern!r}"
            + (f" ab {since.isoformat()}" if since else "")
        )
    return files


def _created(path: Path) -> datetime:
    return _as_utc(datetime.fromisoformat(read_eval_log(str(path), header_only=True).eval.created))


def _as_utc(moment: datetime) -> datetime:
    """Ohne Zeitzone ist UTC gemeint — dieselbe Zeit, die in den Dateinamen steht."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def read_runs(files: list[Path]) -> tuple[list[Run], list[Failed]]:
    """Alle Läufe der Auswahl, dazu die Läufe ohne Ergebnis mit ihrer Ursache."""
    runs: list[Run] = []
    failed: list[Failed] = []
    for path in files:
        log = read_eval_log(str(path), header_only=True)
        headline = log.eval.headline_metric
        score = None
        if log.status == "success" and log.results and headline:
            entry = next((s for s in log.results.scores if s.name == headline.score), None)
            if entry and headline.metric in entry.metrics:
                score = entry
        if score is None:
            failed.append(read_failure(path))
            continue
        stderr = score.metrics.get("stderr")
        runs.append(
            Run(
                file=path.name,
                task=log.eval.task.rsplit("/", 1)[-1],
                display_name=log.eval.task_display_name or log.eval.task,
                version=str(log.eval.task_version),
                model=model_label(log),
                score=score.metrics[headline.metric].value,
                stderr=stderr.value if stderr else None,
                created=log.eval.created,
                task_args=comparable_args(log.eval.task_args),
                sample_ids=json.dumps(sorted(map(str, log.eval.dataset.sample_ids or []))),
                eval_id=log.eval.eval_id,
                epochs=str(log.eval.config.epochs or 1),
                benchmark_version=benchmark_version(log),
            )
        )
    return runs, failed


def benchmark_version(log: EvalLog) -> str:
    """Die Benchmark-Version eines Laufs, wie der Bericht sie nennt; leer, wenn sie fehlt."""
    logged = (log.eval.packages or {}).get(PACKAGE, "")
    return RENAMED_VERSIONS.get(logged, logged)


def read_failure(path: Path) -> Failed:
    """Was ein Lauf ohne Ergebnis über seinen Abbruch verrät.

    Das Log wird dafür ganz gelesen. Die Meldung im Kopf ist oft nur die
    Hülle (`RetryError(<Future … raised AttemptTimeoutError>)`); was wirklich
    passiert ist, steht am Modellaufruf oder am Datensatz.
    """
    log = read_eval_log(str(path))
    calls = [e for s in log.samples or [] for e in s.events if e.event == "model"]
    return Failed(
        file=path.name,
        task=log.eval.task.rsplit("/", 1)[-1],
        model=model_label(log),
        status=log.status,
        cause=_cause(log),
        calls=len(calls) if log.samples else None,
        seconds=wall_seconds(log),
    )


def _cause(log: EvalLog) -> str:
    """Die genaueste Fehlermeldung: Modellaufruf vor Datensatz vor Lauf."""
    samples = log.samples or []
    for sample in reversed(samples):
        for event in reversed(sample.events):
            if event.event == "model" and event.error:
                return _first_line(event.error)
    for sample in samples:
        if sample.error:
            return _first_line(sample.error.message)
    if log.error:
        return _first_line(log.error.message)
    if log.status == "cancelled":
        return "abgebrochen"
    if log.status == "success":
        return "kein Leitwert im Log"
    return log.status


def _first_line(text: str, limit: int = 200) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[:limit - 1] + "…"


# ------------------------------------------------------------- Judge-Urteile
#
# Ein Judge wertet einen fertigen Lauf nachträglich und legt seine Urteile in
# eine Datei neben das Log (`judge.py`: X.eval → X.judges.json). Hier werden
# sie nur eingesammelt und gemittelt, wie die Scores auch: Was der Judge je
# Lauf zusammengefasst hat (`summary.unusable_rate`, der Anteil der Titel, die
# er als unbrauchbar einstuft), gilt als gegeben.
#
# Die Judge-Spalten stehen neben den Aufgaben und gehen nie in den Gesamtscore
# ein. Sie messen etwas anderes (einen Anteil verworfener Titel, kleiner ist
# besser) und sind Diagnose, kein Score.


@dataclass(frozen=True)
class JudgeRun:
    """Das Urteil eines Judges über einen Lauf, so wie es im Sidecar steht."""

    file: str
    task: str
    model: str
    judge: str
    judge_version: int
    # Anteil der Titel, die der Judge als unbrauchbar einstuft. None, wenn er
    # über keinen Titel des Laufs ein lesbares Urteil abgegeben hat. Das ist
    # keine 0.
    unusable_rate: float | None
    parsed: float | None


def read_judges(files: list[Path], runs: list[Run]) -> tuple[list[JudgeRun], list[str]]:
    """Alle Judge-Urteile zu den ausgewählten Läufen, dazu was nicht dazu passt.

    Ein Sidecar, dessen `eval_id` nicht die des Logs daneben ist, gehört zu
    einem anderen Lauf, etwa weil die `.eval` ersetzt wurde. Das fällt nur
    auf, wenn man beide Dateien zusammen liest, also hier.
    """
    by_file = {run.file: run for run in runs}
    judged: list[JudgeRun] = []
    problems: list[str] = []
    for path in files:
        run = by_file.get(path.name)
        side = sidecar_path(path)
        if run is None or not side.exists():
            continue
        data = json.loads(side.read_text(encoding="utf-8"))
        if data.get("eval_id") != run.eval_id:
            problems.append(
                f"{side.name}: gehört zu einem anderen Lauf "
                f"(eval_id {data.get('eval_id')!r}, Log {run.eval_id!r})"
            )
            continue
        for judge, entry in data.get("judges", {}).items():
            summary = entry.get("summary") or {}
            if "unusable_rate" not in summary:
                # Übergangen, fehlte ein Judge ohne Hinweis in der Tabelle.
                problems.append(
                    f"{side.name}: {judge} hat kein Feld unusable_rate "
                    f"(Judge-Version {entry.get('judge_version')}). Neu werten mit "
                    "python -m german_seo_bench.judge, oder den Eintrag entfernen."
                )
                continue
            judged.append(
                JudgeRun(
                    file=path.name,
                    task=run.task,
                    model=run.model,
                    judge=judge,
                    judge_version=int(entry["judge_version"]),
                    unusable_rate=summary["unusable_rate"],
                    parsed=summary.get("parsed"),
                )
            )
    return judged, problems


def check_judges(judged: list[JudgeRun]) -> list[str]:
    """Derselbe Judge mit verschiedenen Prompt-Versionen ist nicht derselbe Judge.

    Jedes Urteil für sich ist gültig. Ihr Mittel wäre aber eines über zwei
    verschiedene Fragen, genau wie bei gemischten Aufgabenversionen.
    """
    problems: list[str] = []
    for task, judge in sorted({(j.task, j.judge) for j in judged}):
        versions: dict[int, list[str]] = {}
        for j in judged:
            if (j.task, j.judge) == (task, judge):
                versions.setdefault(j.judge_version, []).append(j.file)
        if len(versions) > 1:
            lines = [f"{task} · {judge}: verschiedene Judge-Versionen"]
            for version, files in sorted(versions.items()):
                lines.append(f"    v{version}  ({_runs(len(files))})")
                lines.extend(f"      {f}" for f in files)
            problems.append("\n".join(lines))
    return problems


# ---------------------------------------------------------- Zusammenführen


# Aufgabenparameter, die den Leitwert nicht berühren und deshalb beim Vergleich
# der Läufe außen vor bleiben. Ohne diese Liste meldete die Prüfung A01 mit und
# ohne Kundenbericht als verschiedene Benchmarks, obwohl `score` in beiden
# gleich gerechnet wird.
#
#   report   A01: PDF-Bericht für Menschen, keine Messung
#
# Was hier steht, muss der jeweiligen Aufgabe nach wirklich am Leitwert
# vorbeigehen. Ein Parameter, der den Score ändert, gehört nie hierher. Der
# M01-Judge braucht keinen Eintrag: Er läuft getrennt über das fertige Log
# und berührt es nicht.
SCORE_NEUTRAL_ARGS = frozenset({"report"})


def comparable_args(task_args: dict[str, Any]) -> str:
    """Die Aufgabenparameter, die den Leitwert bestimmen, als kanonisches JSON."""
    return json.dumps(
        {k: v for k, v in task_args.items() if k not in SCORE_NEUTRAL_ARGS},
        sort_keys=True,
        default=str,
    )

# Was innerhalb einer Aufgabe über alle Läufe gleich sein muss, mit dem Namen,
# unter dem eine Abweichung gemeldet wird.
SETUP = (
    ("version", "Aufgabenversion"),
    ("task_args", "Aufgabenparameter (-T)"),
    ("sample_ids", "Datensatz-Ausschnitt (--limit/--sample-id)"),
    # Ein Lauf mit drei Epochen steckt dreimal so viele Urteile in seinen Wert
    # wie einer mit einer. Als zwei gleichwertige Wiederholungen gemittelt,
    # zählte der genauere Wert nur halb.
    ("epochs", "Epochenzahl (--epochs)"),
)


def _runs(count: int) -> str:
    return f"{count} {'Lauf' if count == 1 else 'Läufe'}"


def check_consistency(runs: list[Run]) -> list[str]:
    """Wo Läufe einer Aufgabe nicht denselben Benchmark gemessen haben — je Fund ein Text."""
    problems: list[str] = []
    for task in sorted({r.task for r in runs}):
        task_runs = [r for r in runs if r.task == task]
        for attr, label in SETUP:
            values: dict[str, list[str]] = {}
            for run in task_runs:
                values.setdefault(getattr(run, attr), []).append(run.file)
            if len(values) > 1:
                lines = [f"{task}: verschiedene {label}"]
                for value, files in values.items():
                    shown = value if len(value) <= 60 else value[:57] + "..."
                    lines.append(f"    {shown}  ({_runs(len(files))})")
                    lines.extend(f"      {f}" for f in files)
                problems.append("\n".join(lines))
    return problems


@dataclass(frozen=True)
class Cell:
    """Alle Läufe einer Aufgabe auf einem Modell."""

    # Je Datensatz alle Scores über Läufe und Epochen, so wie sie im Log stehen.
    samples: dict[str, tuple[float, ...]]
    # Der Leitwert jedes Laufs aus dem Log. Rohdaten für das JSON, kein Score.
    run_scores: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.run_scores)

    @property
    def worst(self) -> dict[str, float]:
        return {sample: min(scores) for sample, scores in self.samples.items()}

    @property
    def value(self) -> float:
        """Der Score der Zelle: Mittel über die Datensätze des jeweils schlechtesten Laufs."""
        return statistics.fmean(self.worst.values())


@dataclass(frozen=True)
class JudgeCell:
    """Alle Judge-Urteile eines Judges über die Läufe einer Aufgabe auf einem Modell."""

    rates: tuple[float | None, ...]

    @property
    def n(self) -> int:
        return len(self.rates)

    @property
    def rated(self) -> tuple[float, ...]:
        return tuple(r for r in self.rates if r is not None)

    @property
    def mean(self) -> float | None:
        return statistics.fmean(self.rated) if self.rated else None

    @property
    def std(self) -> float | None:
        return statistics.stdev(self.rated) if len(self.rated) > 1 else None


@dataclass(frozen=True)
class JudgeColumn:
    task: str
    judge: str
    judge_version: int

    @property
    def label(self) -> str:
        return f"{self.task} · {self.judge}"


@dataclass(frozen=True)
class ModelRow:
    model: str
    cells: dict[str, Cell]
    missing: tuple[str, ...]
    # Schlüssel ist (Aufgabe, Judge). Leer, wenn kein Lauf dieses Modells
    # gewertet wurde.
    judge_cells: dict[tuple[str, str], JudgeCell] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return statistics.fmean(cell.value for cell in self.cells.values())

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class Task:
    name: str
    display_name: str
    version: str


@dataclass
class Report:
    tasks: list[Task]
    rows: list[ModelRow]
    runs: list[Run]
    selection: dict[str, Any] = field(default_factory=dict)
    judges: list[JudgeColumn] = field(default_factory=list)
    judge_runs: list[JudgeRun] = field(default_factory=list)
    # Jede Antwort mit ihrem Score aus dem Log. Grundlage der Zellen und der
    # Einzelergebnisse; einmal gelesen, an beiden Stellen benutzt.
    answers: list[Answer] = field(default_factory=list)
    # Sampling, Laufzeit, Tempo und Kontext je Lauf (`runstats.py`). Kein
    # Score, nur Beiwerk für den Abschnitt „Laufdaten“.
    stats: list[RunStats] = field(default_factory=list)
    # Läufe ohne Ergebnis. In keiner Zahl enthalten, aber genannt.
    failed: list[Failed] = field(default_factory=list)


def aggregate(runs: list[Run], answers: list[Answer],
              judged: list[JudgeRun] | None = None) -> Report:
    """Läufe → Tabelle. Sortiert nach Gesamtscore, bei Gleichstand nach Name."""
    if not runs:
        raise ReportError("Keine Läufe ausgewählt.")
    judged = judged or []
    problems = check_consistency(runs) + check_modes(runs) + check_judges(judged)
    if problems:
        raise ReportError(_not_comparable(problems))

    tasks = [
        Task(name, next(r.display_name for r in runs if r.task == name),
             next(r.version for r in runs if r.task == name))
        for name in sorted({r.task for r in runs})
    ]
    per_sample: dict[tuple[str, str], dict[str, list[float]]] = {}
    for a in answers:
        if a.score is not None:
            per_sample.setdefault((a.task, a.model), {}).setdefault(a.sample_id, []).append(a.score)
    rows = []
    for model in {r.model for r in runs}:
        cells = {
            t.name: Cell(
                {sample: tuple(scores)
                 for sample, scores in per_sample.get((t.name, model), {}).items()},
                tuple(r.score for r in runs if r.model == model and r.task == t.name),
            )
            for t in tasks
            if any(r.model == model and r.task == t.name for r in runs)
        }
        missing = tuple(t.name for t in tasks if t.name not in cells)
        judge_cells = {
            key: JudgeCell(tuple(j.unusable_rate for j in judged
                                 if j.model == model and (j.task, j.judge) == key))
            for key in {(j.task, j.judge) for j in judged if j.model == model}
        }
        rows.append(ModelRow(model, cells, missing, judge_cells))
    rows.sort(key=lambda row: (-row.overall, row.model))
    judges = [
        JudgeColumn(task, judge, next(j.judge_version for j in judged
                                      if (j.task, j.judge) == (task, judge)))
        for task, judge in sorted({(j.task, j.judge) for j in judged})
    ]
    return Report(tasks, rows, runs, judges=judges, judge_runs=judged, answers=answers)


# ------------------------------------------------------------- Denkmodi
#
# Ein Modell läuft oft in mehreren Denkmodi, jeder unter eigenem Namen mit der
# Endung `-think-<variante>` (`qwen3.8-27b-think-none`, `…-think-high`).
# Gerechnet wird oben je vollem Namen. Hier wird nur umgruppiert: eine Zeile je
# Modell, je Aufgabe eine Spalte für „aus“ und eine für „an“, soweit gelaufen.
# Jede Spalte ist genau eine Messung, jede Zahl steht schon in der Tabelle je
# vollem Namen. Zwei Varianten, die in dieselbe Spalte fielen, brechen ab
# (`check_modes`).
#
# Einen Standardmodus je Aufgabe gibt es mit Absicht nicht. Er setzte eine
# Annahme ("Einzelfragen brauchen kein Nachdenken") als Vorgabe, und genau die
# soll sich mit dem Benchmark prüfen lassen. Welche Modi laufen, entscheidet,
# wer die Läufe startet. Ebenso wenig zählt der beste Modus je Aufgabe: Ein
# Modell mit vier Modi hätte vier Versuche, eines mit einem Modus nur einen —
# derselbe Glücksvorteil, den der schlechteste Lauf je Datensatz ausschließt.
#
# Gibt es von einer Aufgabe mehrere Modi, gibt es deshalb auch mehrere
# Gesamtscores: einen je Kombination aus einer Messung pro Aufgabe.
#
# Ein Name ohne Endung — auch nach `model_label`, das sie bei Cloud-Modellen
# aus dem Log ergänzt — hat keinen bekannten Modus ("?"). Ihn einer Spalte
# zuzuschlagen, etwa „aus“ für ein Modell, das gar nicht denken kann, wäre
# wieder eine Annahme.

# Hinter `-think-` steht die Variante (`THINK_SUFFIX` in `modelname.py`). Die
# Bezeichnungen folgen dem, was das Chat-Template des Modells erwartet:
# `enable_thinking` kennt on/off, `reasoning_effort` none/low/medium/high/xhigh;
# Cloud-Modelle bekommen ihre Endung aus dem Log (`model_label`). Verglichen
# wird trotzdem nur aus gegen an — sonst stünde jedes Modell unter einer
# eigenen Spalte, weil jede Modellfamilie ihr Denken anders schaltet. Ebenso
# bleibt eine Denkgrenze (`-think-on-budget512`) „an“.
MODE_OFF = "aus"
MODE_ON = "an"
# Reihenfolge der Modi je Aufgabe, immer dieselbe; ohne Endung zuletzt.
# Nicht nach der Zahl der Modelle: Dann stünde „an“ bei einer Aufgabe vor
# „aus“ und bei der nächsten dahinter, und man läse die Spalten verwechselt.
MODE_ORDER = (MODE_OFF, MODE_ON, None)


def split_model(model: str) -> tuple[str, str | None]:
    """Modellname ohne Denkmodus, und der Modus („aus“/„an“) — None ohne Endung."""
    match = THINK_SUFFIX.match(model)
    if not match:
        return model, None
    word = match["variant"].split("-", 1)[0]
    return match["base"], MODE_OFF if word in THINK_OFF else MODE_ON


def check_modes(runs: list[Run]) -> list[str]:
    """Varianten eines Modells, die für dieselbe Aufgabe im selben Modus landen.

    `think-low` und `think-high` sind beide „an“. Liefen beide dieselbe
    Aufgabe, gehörten zwei Messungen in eine Zelle. Eine davon zu nehmen, wäre
    eine stille Auswahl; den schlechtesten Lauf über beide zu nehmen, mischte
    zwei Einstellungen zu einer Zahl, die keiner von beiden entspricht.
    """
    variants: dict[tuple[str, str, str], dict[str, list[str]]] = {}
    for run in runs:
        base, mode = split_model(run.model)
        if mode is None:
            continue
        variants.setdefault((base, mode, run.task), {}).setdefault(run.model, []).append(run.file)
    problems = []
    for (base, mode, task), models in sorted(variants.items()):
        if len(models) > 1:
            lines = [f"{task}: mehrere Varianten von {base} im Modus „{mode}“"]
            for model, files in sorted(models.items()):
                lines.append(f"    {model}  ({_runs(len(files))})")
                lines.extend(f"      {f}" for f in files)
            problems.append("\n".join(lines))
    return problems


@dataclass(frozen=True)
class Column:
    """Eine Messung: eine Aufgabe in einem Denkmodus."""

    task: str
    mode: str | None
    # Ohne Zusammenfassung trägt die Spalte nur den Aufgabennamen.
    split: bool = False

    @property
    def label(self) -> str:
        return f"{self.task} · {self.mode or '?'}" if self.split else self.task


@dataclass(frozen=True)
class Total:
    """Eine Gesamtspalte: je Aufgabe genau eine Messung."""

    columns: tuple[Column, ...]
    # Leer, solange es nur eine Gesamtspalte gibt.
    key: str = ""

    @property
    def label(self) -> str:
        return f"Gesamt [{self.key}]" if self.key else "Gesamt"

    @property
    def legend(self) -> str:
        return ", ".join(c.label for c in self.columns)


@dataclass(frozen=True)
class Overall:
    value: float
    # Aufgaben, die das Modell in keinem Modus hat.
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class MatrixJudge:
    column: Column
    judge: str
    judge_version: int

    @property
    def label(self) -> str:
        return f"{self.column.label} · {self.judge}"


@dataclass(frozen=True)
class MatrixRow:
    model: str
    # Die vollen Modellnamen, aus denen die Zeile besteht.
    variants: tuple[str, ...]
    cells: dict[Column, Cell]
    # Der volle Name hinter jeder Zelle, für Tooltip und JSON.
    sources: dict[Column, str]
    judge_cells: dict[tuple[Column, str], JudgeCell]
    missing: tuple[str, ...]

    def overall(self, total: Total) -> Overall | None:
        """Mittel der Messungen dieser Gesamtspalte, oder None.

        None, wenn das Modell eine ihrer Aufgaben nur in einem anderen Modus
        hat. Aus den übrigen Aufgaben gerechnet, stünde dort dieselbe Zahl wie
        in der Nachbarspalte, nur unter einer Kombination, die nie gemessen
        wurde. Fehlt die Aufgabe ganz, gilt wie überall: Gesamt aus den
        vorhandenen, als unvollständig markiert.
        """
        values: list[float] = []
        for column in total.columns:
            if column in self.cells:
                values.append(self.cells[column].value)
            elif column.task not in self.missing:
                return None
        if not values:
            return None
        return Overall(statistics.fmean(values), self.missing)


@dataclass
class Matrix:
    """Eine Tabelle des Berichts: Zeilen, Messungen, Gesamtspalten."""

    columns: list[Column]
    totals: list[Total]
    rows: list[MatrixRow]
    judges: list[MatrixJudge]
    task_count: int
    split: bool


def build_matrix(report: Report, split: bool) -> Matrix:
    """Die Tabelle je vollem Modellnamen (`split=False`) oder je Modell.

    Rechnet nichts: Jede Zelle ist die `Cell` aus `report.rows`, nur unter
    einer anderen Zeile und Spalte einsortiert.
    """
    grouped: dict[str, list[tuple[ModelRow, str | None]]] = {}
    for row in report.rows:
        base, mode = split_model(row.model) if split else (row.model, None)
        grouped.setdefault(base, []).append((row, mode))

    rows: list[MatrixRow] = []
    for base, members in grouped.items():
        cells: dict[Column, Cell] = {}
        sources: dict[Column, str] = {}
        judge_cells: dict[tuple[Column, str], JudgeCell] = {}
        for row, mode in members:
            for task, cell in row.cells.items():
                column = Column(task, mode, split)
                cells[column] = cell
                sources[column] = row.model
            for (task, judge), judge_cell in row.judge_cells.items():
                judge_cells[(Column(task, mode, split), judge)] = judge_cell
        missing = tuple(t.name for t in report.tasks
                        if not any(c.task == t.name for c in cells))
        rows.append(MatrixRow(base, tuple(sorted(r.model for r, _ in members)),
                              cells, sources, judge_cells, missing))

    # Je Aufgabe „aus“, „an“, "?" — in jeder Aufgabe gleich.
    per_task = [
        sorted({c for row in rows for c in row.cells if c.task == task.name},
               key=lambda c: MODE_ORDER.index(c.mode))
        for task in report.tasks
    ]
    columns = [c for group in per_task for c in group]

    # Gesamtspalten, in denen kein Modell einen Wert hat, fallen weg. Bei einem
    # Modus je Aufgabe bleibt genau eine übrig, wie ohne Zusammenfassung.
    candidates = [Total(combo) for combo in itertools.product(*per_task)]
    counted = [(t, sum(row.overall(t) is not None for row in rows)) for t in candidates]
    kept = [t for t, n in sorted(counted, key=lambda pair: -pair[1]) if n > 0]
    if len(kept) > 1:
        kept = [Total(t.columns, _key(i)) for i, t in enumerate(kept)]

    # Sortiert nach der ersten Gesamtspalte, ohne Wert dort ans Ende.
    def rank(row: MatrixRow) -> tuple[bool, float, str]:
        overall = row.overall(kept[0])
        return overall is None, -overall.value if overall else 0.0, row.model

    rows.sort(key=rank)
    judges = [
        MatrixJudge(column, j.judge, j.judge_version)
        for j in report.judges
        for column in columns
        if column.task == j.task and any((column, j.judge) in row.judge_cells for row in rows)
    ]
    return Matrix(columns, kept, rows, judges, len(report.tasks), split)


def _key(index: int) -> str:
    """a, b, …, z, aa, ab, … — Kennung einer Gesamtspalte."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("a") + rest) + letters
    return letters


def combined_matrix(report: Report) -> Matrix | None:
    """Die Zusammenfassung je Modell; None, wenn kein Name einen Modus trägt."""
    if not any(split_model(row.model)[1] for row in report.rows):
        return None
    return build_matrix(report, split=True)


def sections(report: Report) -> list[tuple[str | None, Matrix]]:
    """Die Tabellen des Berichts, jede mit Überschrift.

    Die Tabelle je vollem Namen steht nur dann zusätzlich da, wenn die
    Zusammenfassung wirklich etwas zusammenlegt. Hat jedes Modell nur einen
    Modus, wären beide Tabellen dieselbe.
    """
    combined = combined_matrix(report)
    flat = build_matrix(report, split=False)
    if combined is None:
        return [(None, flat)]
    result: list[tuple[str | None, Matrix]] = [("Je Modell, Denkmodi als Spalten", combined)]
    if any(len(row.variants) > 1 for row in combined.rows):
        result.append(("Je Modellvariante", flat))
    return result


# ---------------------------------------------------------------- Ausgabe


def _cell_text(cell: Cell | None, uniform_n: bool) -> str:
    if cell is None:
        return "—"
    text = f"{cell.value:.2f}"
    if not uniform_n:
        text += f" (n={cell.n})"
    return text


def _uniform_n(report: Report) -> int | None:
    counts = {cell.n for row in report.rows for cell in row.cells.values()}
    return counts.pop() if len(counts) == 1 else None


def _judge_text(row: MatrixRow, judge: MatrixJudge) -> str:
    """Anteil unbrauchbar laut Judge; „?“ ohne lesbares Urteil, „—“ ohne Judge-Lauf.

    Die Zahl der Läufe steht dabei, sobald sie von der der Messung abweicht:
    Hat der Judge nur einen von drei Läufen gewertet, sagt die Zelle sonst
    mehr, als sie weiß.
    """
    cell = row.judge_cells.get((judge.column, judge.judge))
    if cell is None:
        return "—"
    if cell.mean is None:
        return "?"
    text = f"{cell.mean * 100:.0f} %"
    if cell.std is not None:
        text += f" ±{cell.std * 100:.0f}"
    measured = row.cells.get(judge.column)
    if measured is None or cell.n != measured.n:
        text += f" ({cell.n} von {measured.n if measured else 0} Läufen)"
    return text


def _overall_text(overall: Overall | None) -> str:
    if overall is None:
        return "—"
    return f"{overall.value:.2f}" + ("" if overall.complete else "*")


def _table(matrix: Matrix, uniform: bool) -> tuple[list[str], list[list[str]]]:
    """Kopf und Zeilen einer Tabelle — eine Quelle für Terminal und Markdown."""
    header = [
        "Modell", *(t.label for t in matrix.totals), *(c.label for c in matrix.columns),
        *(f"Judge {j.label}" for j in matrix.judges),
    ]
    body = [
        [row.model, *(_overall_text(row.overall(t)) for t in matrix.totals),
         *(_cell_text(row.cells.get(c), uniform) for c in matrix.columns),
         *(_judge_text(row, j) for j in matrix.judges)]
        for row in matrix.rows
    ]
    return header, body


def _matrix_notes(matrix: Matrix) -> list[str]:
    """Was zu genau dieser Tabelle gehört."""
    total = matrix.task_count
    notes = [
        f"* {row.model}: Gesamt aus {total - len(row.missing)} von {total} Aufgaben, "
        f"ohne {', '.join(row.missing)}"
        for row in matrix.rows
        if row.missing
    ]
    if matrix.split:
        text = ("Eine Zeile je Modell, je Aufgabe eine Spalte pro Denkmodus; der Modus "
                "stammt aus der Endung des Modellnamens: -think-off und -think-none = aus, "
                "jede andere -think-… = an. Bei Cloud-Modellen ergänzt aus reasoning_effort "
                "im Log; -think-adaptive = Claude ab 4.7 ohne Angabe, denkt nach Vorgabe der API.")
        if any(BUDGET_MARK in m for row in matrix.rows for m in row.variants):
            text += (" -budget<n> = Denkgrenze von n Tokens je Antwort, aus "
                     "thinking_budget_tokens in der Anfrage.")
        if any(c.mode is None for c in matrix.columns):
            text += " ? = Modellname ohne Endung, Modus nicht angegeben."
        notes.append(text)
        notes += _variant_notes(matrix)
    if len(matrix.totals) > 1:
        notes.append("Je Gesamtspalte eine Messung pro Aufgabe: "
                     + "; ".join(f"[{t.key}] {t.legend}" for t in matrix.totals))
        if any(row.overall(t) is None for row in matrix.rows for t in matrix.totals):
            notes.append("— als Gesamt: Das Modell hat eine Aufgabe dieser Spalte nur in "
                         "einem anderen Modus.")
    return notes


def _variant_notes(matrix: Matrix) -> list[str]:
    """Je Modell die Endung hinter jedem Modus, z. B. „an = think-on-precise“.

    Die Spalte sagt nur aus oder an; welche Einstellung dahintersteht —
    `think-low` oder `think-high` —, gehört zur Messung und stünde sonst nur
    im Tooltip. Läuft ein Modell im selben Modus je Aufgabe mit anderer
    Endung, steht die Aufgabe dabei.
    """
    notes = []
    for row in matrix.rows:
        per_mode: dict[str | None, dict[str, list[str]]] = {}
        for column in matrix.columns:
            if column in row.sources:
                suffix = row.sources[column][len(row.model) + 1:] or "ohne Endung"
                per_mode.setdefault(column.mode, {}).setdefault(suffix, []).append(column.task)
        parts = []
        for mode in MODE_ORDER:
            variants = per_mode.get(mode, {})
            texts = [suffix if len(variants) == 1 else f"{suffix} ({', '.join(tasks)})"
                     for suffix, tasks in variants.items()]
            if texts:
                parts.append(f"{mode or '?'} = {', '.join(texts)}")
        if parts:
            notes.append(f"{row.model}: {'; '.join(parts)}")
    return notes


def benchmark_versions(runs: list[Run]) -> dict[str, int]:
    """Benchmark-Version → Zahl der Läufe, aufsteigend sortiert."""
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.benchmark_version] = counts.get(run.benchmark_version, 0) + 1
    return dict(sorted(counts.items()))


def benchmark_text(runs: list[Run]) -> str:
    """„german-seo-bench 2026.1“; bei mehreren Ständen jeder mit seinen Läufen."""
    versions = benchmark_versions(runs)
    if len(versions) == 1:
        return f"{PACKAGE} {next(iter(versions)) or 'unbekannt'}"
    return f"{PACKAGE} " + ", ".join(
        f"{version or 'unbekannt'} ({_runs(count)})" for version, count in versions.items()
    )


MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember")


def measurement_period(runs: list[Run]) -> tuple[datetime, datetime]:
    """Start des frühesten und des spätesten Laufs, in UTC wie die Dateinamen."""
    starts = [_as_utc(datetime.fromisoformat(run.created)).astimezone(timezone.utc)
              for run in runs]
    return min(starts), max(starts)


def period_text(runs: list[Run]) -> str:
    """„August bis Oktober 2026“, auf Monate genau.

    Ein Tag bringt dem Leser nichts und täuscht eine Genauigkeit vor, die
    bei nachgemessenen Modellen nicht besteht.
    """
    first, last = measurement_period(runs)
    end = f"{MONTHS[last.month - 1]} {last.year}"
    if (first.year, first.month) == (last.year, last.month):
        return end
    start = MONTHS[first.month - 1] + ("" if first.year == last.year else f" {first.year}")
    return f"{start} bis {end}"


def _footer_line(runs: list[Run]) -> str:
    """Die Zeile unter den SVGs: woher die Zahlen stammen und wann sie entstanden."""
    return f"{benchmark_text(runs)} · Messzeitraum {period_text(runs)}"


def _common_notes(report: Report) -> list[str]:
    """Was für alle Tabellen des Berichts gilt."""
    n = _uniform_n(report)
    runs = f"je {n} Lauf" if n == 1 else f"je {n} Läufe" if n else "Läufe je Zelle in Klammern"
    notes = [
        f"Grundlage: {_runs(len(report.runs))}, {runs}. "
        "Score je Aufgabe = Mittel über die Datensätze, je Datensatz der schlechteste Lauf"
        + (" (je mehr Läufe, desto strenger)" if n != 1 else "")
        + ". Gesamt = arithmetisches Mittel der Aufgabenscores.",
        "Benchmark-Version: " + benchmark_text(report.runs),
        "Messzeitraum: " + period_text(report.runs),
        "Aufgaben: " + ", ".join(f"{t.name} (v{t.version}, {t.display_name})" for t in report.tasks),
    ]
    if report.judges:
        notes.append(
            "Judge-Spalten: Anteil der Titel, die der Judge als unbrauchbar einstuft, "
            "gemittelt über die Läufe; nicht im Gesamt. ? = kein lesbares Urteil. Judges: "
            + ", ".join(f"{c.label} (Prompt v{c.judge_version})" for c in report.judges)
        )
    return notes


def failure_lines(failed: list[Failed]) -> list[str]:
    """Je Lauf ohne Ergebnis eine Zeile: wer, was, woran, wie weit er kam."""
    return [f"{f.model} · {f.task}: {f.cause}{_failure_detail(f)}  [{f.file}]" for f in failed]


def _failure_detail(failed: Failed) -> str:
    parts = []
    if failed.calls:
        parts.append(f"{failed.calls} Modellaufrufe")
    if failed.seconds is not None:
        parts.append(f"Laufzeit {_duration(failed.seconds)}")
    return f" ({', '.join(parts)})" if parts else ""


def view_hint(log_dir: Path | str, code: str = "") -> str:
    return f"Im Einzelnen ansehen: {code}uv run inspect view --log-dir {log_dir}{code}"


def _failed_notes(report: Report) -> list[str]:
    if not report.failed:
        return []
    return [f"Ohne Ergebnis, in keiner Zahl enthalten ({_runs(len(report.failed))}):",
            *(f"  {line}" for line in failure_lines(report.failed)),
            view_hint(report.selection.get("log_dir", "logs"))]


def render_failed(report: Report) -> str:
    """Abschnitt „Abgebrochene Läufe“ für report.md; leer, wenn es keine gibt."""
    if not report.failed:
        return ""
    rows = [
        [f.model, f.task, f.status, f.cause.replace("|", "\\|"),
         str(f.calls) if f.calls is not None else "—", _duration(f.seconds), f"`{f.file}`"]
        for f in report.failed
    ]
    header = ["Modell", "Aufgabe", "Status", "Ursache", "Modellaufrufe", "Laufzeit", "Datei"]
    table = "\n".join("| " + " | ".join(r) + " |"
                      for r in [header, [":---"] * 3 + [":---", "---:", "---:", ":---"], *rows])
    return ("## Abgebrochene Läufe\n\n"
            "Diese Läufe haben kein Ergebnis und stecken in keiner Zahl des Berichts. "
            "Ein Modell ganz ohne Ergebnis fehlt in den Tabellen; fehlt ihm nur eine "
            "Aufgabe, ist sein Gesamt mit * markiert.\n\n"
            f"{table}\n\n{view_hint(report.selection.get('log_dir', 'logs'), '`')}\n")


def _line(cells: list[str], widths: list[int]) -> str:
    return "  ".join(
        c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths))
    )


def render_terminal(report: Report) -> str:
    uniform = _uniform_n(report) is not None
    out: list[str] = []
    for heading, matrix in sections(report):
        header, body = _table(matrix, uniform)
        widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
        if heading:
            out += [heading, ""]
        out += [_line(header, widths), "  ".join("-" * w for w in widths),
                *(_line(r, widths) for r in body), ""]
        if notes := _matrix_notes(matrix):
            out += [*notes, ""]
    failed = _failed_notes(report)
    return "\n".join(out + _common_notes(report) + ([""] + failed if failed else []))


def render_markdown(report: Report) -> str:
    """Pipe-Tabellen, wie Hugo (Goldmark) sie ohne Erweiterung rendert."""
    uniform = _uniform_n(report) is not None
    parts: list[str] = []
    for heading, matrix in sections(report):
        header, body = _table(matrix, uniform)
        align = [":---", *("---:" for _ in header[1:])]
        table = "\n".join("| " + " | ".join(r) + " |" for r in [header, align, *body])
        parts += ([f"## {heading}"] if heading else []) + [table, *_matrix_notes(matrix)]
    return "\n\n".join(parts + _common_notes(report)) + "\n"


# ------------------------------------------------------------- Laufdaten
#
# Was ein Lauf gekostet hat und unter welchen Bedingungen er lief. Keine dieser
# Zahlen ist ein Score, und keine geht in einen ein; woher jede stammt, steht
# in `runstats.py`. Zeilen in derselben Reihenfolge wie die Scoretabelle, damit
# sich beide nebeneinander lesen lassen.

PARTLY_TIMED = "¹"
CONTEXT_FULL = "²"
INCOMPLETE = "*"


def _stats_cells(report: Report) -> dict[str, dict[str, StatsCell]]:
    cells: dict[str, dict[str, list[RunStats]]] = {}
    for run in report.stats:
        cells.setdefault(run.model, {}).setdefault(run.task, []).append(run)
    return {model: {task: StatsCell(tuple(runs)) for task, runs in tasks.items()}
            for model, tasks in cells.items()}


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes, rest = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def _rate(cell: StatsCell, value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}" + (PARTLY_TIMED if cell.partly_timed else "")


def _context(cell: StatsCell) -> str:
    if cell.max_context is None:
        return "—"
    return str(cell.max_context) + (CONTEXT_FULL if cell.context_full else "")


# Je Kennzahl: Überschrift, Kopf der Gesamtspalte, Text einer Zelle, Text der
# Gesamtspalte. Die Gesamtspalte rechnet je Kennzahl anders, und genau so,
# wie man die Zahl braucht: Laufzeit als Summe (ein Durchgang durch alle
# Aufgaben), Tempo über alle Tokens, Kontext als Maximum (so groß muss der
# Server eingestellt sein).
STATS_TABLES = (
    ("Laufzeit je Lauf (Median, m:ss)", "ein Durchgang",
     lambda c: _duration(c.seconds),
     lambda cs: _duration(sum(c.seconds for c in cs if c.seconds is not None)
                          if any(c.seconds is not None for c in cs) else None)),
    ("Generierung (Tokens/s)", "alle Aufgaben",
     lambda c: _rate(c, c.generation_rate),
     lambda cs: _rate(combine(cs), combine(cs).generation_rate)),
    ("Prompt-Verarbeitung (Tokens/s)", "alle Aufgaben",
     lambda c: _rate(c, c.prompt_rate),
     lambda cs: _rate(combine(cs), combine(cs).prompt_rate)),
    ("Größter Kontext (Tokens)", "alle Aufgaben",
     lambda c: _context(c),
     lambda cs: _context(combine(cs))),
)


def _md_table(rows: list[list[str]], numeric: bool = True) -> str:
    align = [":---", *(("---:" if numeric else ":---") for _ in rows[0][1:])]
    return "\n".join("| " + " | ".join(r) + " |" for r in [rows[0], align, *rows[1:]])


def render_runstats(report: Report) -> str:
    """Abschnitt „Laufdaten“ als Markdown: Sampling, dann je Kennzahl eine Tabelle."""
    if not report.stats:
        return ""
    models = [row.model for row in report.rows]
    tasks = [t.name for t in report.tasks]
    cells = _stats_cells(report)
    parts = [
        "## Laufdaten",
        "Nicht Teil des Scores. Alle Angaben stammen aus den Logs: Sampling laut "
        "llama-server (`/props`, von Werten des Laufs überschrieben), Laufzeit als "
        "Wanduhr des ganzen Laufs, Tokens/s aus der Zeitmessung des Servers "
        "(`timings`), nach Tokens gewichtet, Kontext als größtes `total_tokens` "
        "eines Modellaufrufs. — = im Log nicht enthalten.",
        "### Sampling",
    ]
    sampling = sampling_by_model(report.stats)
    rows = [["Modell", "Sampling laut Server"]]
    for model in models:
        settings = sampling.get(model, {})
        if len(settings) > 1:
            text = "; ".join(f"{s} ({', '.join(t)})" for s, t in settings.items())
        else:
            text = next(iter(settings), "—")
        rows.append([model, text])
    parts.append(_md_table(rows, numeric=False))
    if any(len(v) > 1 for v in sampling.values()):
        parts.append("Mehrere Einstellungen bei einem Modell: In Klammern die Aufgaben, "
                     "in denen sie galten.")
    partly = full = incomplete = False
    for title, total_label, cell_text, total_text in STATS_TABLES:
        rows = [["Modell", total_label, *tasks]]
        for model in models:
            mine = cells.get(model, {})
            total = total_text(list(mine.values()))
            if len(mine) < len(tasks) and total != "—":
                total += INCOMPLETE
                incomplete = True
            rows.append([model, total,
                         *(cell_text(mine[t]) if t in mine else "—" for t in tasks)])
            partly |= any(c.partly_timed for c in mine.values())
            full |= any(c.context_full for c in mine.values())
        parts += [f"### {title}", _md_table(rows)]
    if partly:
        partial = [c for m in cells.values() for c in m.values() if c.partly_timed]
        parts.append(
            f"{PARTLY_TIMED} Aus einem Teil der Modellaufrufe "
            f"({sum(c.timed_calls for c in partial)} von {sum(c.calls for c in partial)} "
            "in diesen Zellen): Inspect hebt die Rohantwort mit der Zeitmessung nur für "
            "die ersten Aufrufe eines Datensatzes auf. Bei Agenten-Aufgaben sind das die "
            "ersten Schritte, mit noch kurzem Kontext."
        )
    if full:
        parts.append(f"{CONTEXT_FULL} Das Kontextfenster des Servers (`n_ctx`) ist ausgeschöpft: "
                     "Mindestens ein Aufruf stieß an die Grenze, der wirkliche Bedarf liegt "
                     "darüber.")
    if incomplete:
        parts.append(f"{INCOMPLETE} Nicht alle Aufgaben gelaufen; die Gesamtspalte gilt nur "
                     "für die vorhandenen.")
    return "\n\n".join(parts) + "\n"


def _stats_json(report: Report) -> dict[str, Any]:
    cells = _stats_cells(report)
    return {
        "sampling": sampling_by_model(report.stats),
        "cells": {
            model: {
                task: {
                    "seconds_median": cell.seconds,
                    "generation_tokens_per_s": cell.generation_rate,
                    "prompt_tokens_per_s": cell.prompt_rate,
                    "max_context": cell.max_context,
                    "context_full": cell.context_full,
                    "timed_calls": cell.timed_calls,
                    "calls": cell.calls,
                }
                for task, cell in tasks.items()
            }
            for model, tasks in cells.items()
        },
        "runs": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(r).items()}
                 for r in report.stats],
    }


def render_json(report: Report) -> str:
    data = {
        "aggregation": AGGREGATION,
        "benchmark_versions": benchmark_versions(report.runs),
        "measurement_period": dict(zip(("first", "last"), (
            moment.isoformat() for moment in measurement_period(report.runs)))),
        "selection": report.selection,
        "tasks": [vars(t) for t in report.tasks],
        "models": [
            {
                "model": row.model,
                "overall": row.overall,
                "complete": row.complete,
                "tasks_scored": len(row.cells),
                "tasks_missing": list(row.missing),
                "cells": {
                    name: {
                        "score": cell.value,
                        "n": cell.n,
                        "samples": cell.worst,
                        "run_scores": list(cell.run_scores),
                    }
                    for name, cell in row.cells.items()
                },
            }
            for row in report.rows
        ],
        "runs": [
            {k: v for k, v in vars(run).items()
             if k not in ("task_args", "sample_ids", "display_name", "eval_id")}
            for run in report.runs
        ],
        "judges": [
            {"task": c.task, "judge": c.judge, "judge_version": c.judge_version}
            for c in report.judges
        ],
        "judge_runs": [vars(j) for j in report.judge_runs],
        "combined": _combined_json(combined_matrix(report)),
        "run_stats": _stats_json(report),
        "failed": [vars(f) for f in report.failed],
    }
    for entry, row in zip(data["models"], report.rows):
        judge_cells: dict[str, dict[str, Any]] = {}
        for (task, judge), cell in row.judge_cells.items():
            judge_cells.setdefault(task, {})[judge] = {
                "mean": cell.mean,
                "std": cell.std,
                "n": cell.n,
                "rates": list(cell.rates),
            }
        entry["judge_cells"] = judge_cells
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _combined_json(matrix: Matrix | None) -> dict[str, Any] | None:
    """Die Zusammenfassung je Modell. Die Werte je Datensatz stehen unter `models`."""
    if matrix is None:
        return None

    def overall(o: Overall | None) -> dict[str, Any] | None:
        return None if o is None else {"score": o.value, "complete": o.complete,
                                       "tasks_missing": list(o.missing)}

    return {
        "columns": [{"label": c.label, "task": c.task, "mode": c.mode} for c in matrix.columns],
        "totals": [{"label": t.label, "columns": [c.label for c in t.columns]}
                   for t in matrix.totals],
        "models": [
            {
                "model": row.model,
                "variants": list(row.variants),
                "totals": {t.label: overall(row.overall(t)) for t in matrix.totals},
                "cells": {
                    c.label: {"score": cell.value, "n": cell.n, "source": row.sources[c]}
                    for c, cell in row.cells.items()
                },
            }
            for row in matrix.rows
        ],
    }


# --------------------------------------------------------------------- SVG
#
# Ohne Abhängigkeit geschrieben: matplotlib brächte rund 40 MB in ein Paket, das
# sonst 90 MB wiegt, für eine Handvoll Diagrammformen. Farben aus einer festen,
# auf Lesbarkeit geprüften Palette; Hell und Dunkel als eigene Stufen, per
# Media-Query im SVG selbst — das wirkt auch, wenn Hugo die Datei als <img>
# einbindet.
#
# Alle Diagramme zeigen die erste Tabelle des Berichts: die Zusammenfassung
# je Modell, wo es sie gibt.

STYLE = """
svg { --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --grid:#e4e3df; --bar:#2a78d6; --hatch:#9ec5f4; --accent:#c4520f; }
@media (prefers-color-scheme: dark) {
  svg { --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --grid:#383835; --bar:#3987e5; --hatch:#184f95; --accent:#f08a4b; }
}
text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--ink); }
.muted { fill: var(--ink2); }
.halo { paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
"""

# Sequenzielle Rampe Blau 100–700: niedrig hell, hoch dunkel. Eine Farbe, kein
# Regenbogen — die Zahl steht ohnehin in der Zelle.
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
        "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

# Zeichenbreite bei 13px für die Namensspalte. Ein SVG kann Text nicht messen;
# geschätzt wird deshalb etwas über der Breite einer Monospace-Ersatzschrift
# (7.8 px), nicht mit der schmaleren Systemschrift. Mit dieser schnitte die
# Spalte lange Modellnamen links ab, sobald die Ersatzschrift greift.
CHAR = 8.0


def _ramp(value: float) -> tuple[str, str]:
    """Füllfarbe und Schriftfarbe für einen Wert zwischen 0 und 1."""
    i = round(max(0.0, min(1.0, value)) * (len(RAMP) - 1))
    return RAMP[i], ("#ffffff" if i >= 6 else "#0b0b0b")


def _svg(width: float, height: float, title: str, body: list[str]) -> str:
    # Breit genug für die Überschrift, die als Titel oben links steht.
    width = max(width, len(title) * 8.6)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img">\n'
        f"<title>{escape(title)}</title>\n<style>{STYLE}</style>\n"
        f'<rect width="100%" height="100%" fill="var(--surface)"/>\n'
        + "\n".join(body) + "\n</svg>\n"
    )


def _mode_title(columns: list[Column]) -> str:
    """Zusatz zum Grafiktitel: mit oder ohne Denken gemessen.

    Leer, sobald eine Spalte keinen bekannten Modus hat. Die Einstellungen im
    Einzelnen (Denkgrenze, reasoning_effort) gehören in den Text um die Grafik.
    """
    modes = {c.mode for c in columns}
    if not modes or None in modes:
        return ""
    if modes == {MODE_OFF}:
        return " · ohne Denken"
    if modes == {MODE_ON}:
        return " · mit Denken"
    return " · mit und ohne Denken"


def _legend(matrix: Matrix) -> list[str]:
    """Die Kombinationen der Gesamtspalten, eine Zeile je Spalte."""
    if len(matrix.totals) < 2:
        return []
    return [f"[{t.key}] {t.legend}" for t in matrix.totals]


def render_svg_overall(report: Report) -> str:
    """Gesamtscore je Modell als waagerechte Balken, Achse fest von 0 bis 1.

    Gibt es mehrere Gesamtspalten, bekommt jedes Modell eine Balkengruppe mit
    einem Balken je Spalte.
    """
    matrix = sections(report)[0][1]
    totals = matrix.totals
    many = len(totals) > 1
    label_w = max(len(r.model) for r in matrix.rows) * CHAR + 24
    plot_w, bar_h, top = 420, 16, 40
    pitch = bar_h + 4
    row_h = 30 if not many else pitch * len(totals) + 10
    note_w = 190 if many else 150
    width = label_w + plot_w + note_w
    legend = _legend(matrix)
    axis_y = top + row_h * len(matrix.rows)
    incomplete = any(r.missing for r in matrix.rows)
    title = "Gesamtscore je Modell" + _mode_title(matrix.columns)
    footer = (["Schraffiert: nicht alle Aufgaben gelaufen, Gesamt über die vorhandenen."]
              if incomplete else []) + legend + [_footer_line(report.runs)]
    height = axis_y + 34 + 16 * max(0, len(footer) - 1)
    width = max(width, max((len(f) for f in footer), default=0) * 6.2)
    body = [
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--hatch)"/>'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="var(--bar)" stroke-width="2"/></pattern></defs>',
        f'<text x="0" y="18" font-size="15" font-weight="600">{escape(title)}</text>',
    ]
    for tick in (0, 0.25, 0.5, 0.75, 1):
        x = label_w + tick * plot_w
        body.append(f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{axis_y}" stroke="var(--grid)"/>')
        body.append(f'<text x="{x:.1f}" y="{axis_y + 16}" font-size="11" class="muted" '
                    f'text-anchor="middle">{tick:g}</text>')
    for i, row in enumerate(matrix.rows):
        y0 = top + i * row_h + (row_h - pitch * len(totals) + 4) / 2
        body.append(f'<text x="{label_w - 10}" y="{y0 + (pitch * len(totals) - 4) / 2 + 5:.1f}" '
                    f'font-size="13" text-anchor="end">{escape(row.model)}</text>')
        for k, total in enumerate(totals):
            y = y0 + k * pitch
            overall = row.overall(total)
            prefix = f"[{total.key}] " if many else ""
            if overall is None:
                body.append(f'<text x="{label_w + 6}" y="{y + bar_h - 3}" font-size="12" '
                            f'class="muted">{escape(prefix)}—</text>')
                continue
            w = max(overall.value * plot_w, 0.0)
            fill = "var(--bar)" if overall.complete else "url(#hatch)"
            note = ("" if overall.complete
                    else f" · {matrix.task_count - len(overall.missing)}/{matrix.task_count} Aufgaben")
            tip = f"{row.model} · {total.label}: {overall.value:.3f}{note}"
            body.append(f"<g><title>{escape(tip)}</title>")
            if w > 0:
                # Rund nur am Datenende; die Basis bleibt eckig an der Nulllinie.
                r = min(4, w)
                body.append(
                    f'<path d="M{label_w},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} '
                    f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z" fill="{fill}"/>'
                )
            body.append(f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h - 3}" font-size="12">'
                        f'{escape(prefix)}{overall.value:.2f}<tspan class="muted">{escape(note)}'
                        "</tspan></text>")
            body.append("</g>")
    for k, text in enumerate(footer):
        body.append(f'<text x="0" y="{axis_y + 30 + 16 * k}" font-size="11" class="muted">'
                    f"{escape(text)}</text>")
    return _svg(width, height, title, body)


def render_svg_matrix(report: Report) -> str:
    """Modell × Messung als Heatmap, Gesamt als eigene, abgesetzte Spalten."""
    matrix = sections(report)[0][1]
    label_w = max(len(r.model) for r in matrix.rows) * CHAR + 24
    cell_w, cell_h, gap = 104, 30, 2
    # Mit Denkmodi steht der Modus als zweite Kopfzeile unter der Aufgabe.
    top = 66 if matrix.split else 52
    total_x = label_w + len(matrix.columns) * (cell_w + gap) + 14
    # Judge-Spalten als eigener Block hinter Gesamt, abgesetzt wie Gesamt
    # selbst: anderes Maß (Anteil verworfen), anderer Charakter (Diagnose).
    judge_x = total_x + len(matrix.totals) * (cell_w + gap) + 12
    width = (judge_x + len(matrix.judges) * (cell_w + gap) + 2) if matrix.judges else judge_x - 10
    rows_end = top + len(matrix.rows) * (cell_h + gap)
    footer = ((["* Gesamt ohne die fehlenden Aufgaben (gestrichelt)."]
               if any(r.missing for r in matrix.rows) else []) + _legend(matrix)
              + [_footer_line(report.runs)])
    height = rows_end + 14 + 16 * len(footer)
    width = max(width, max((len(f) for f in footer), default=0) * 6.2)
    title = "Score je Aufgabe und Modell" + _mode_title(matrix.columns)
    body = [f'<text x="0" y="18" font-size="15" font-weight="600">{escape(title)}</text>']

    def head(x: float, title: str, sub: str | None, bold: bool = False) -> None:
        weight = ' font-weight="600"' if bold else ""
        if sub is None:
            body.append(f'<text x="{x:.1f}" y="{top - 10}" font-size="12"{weight} '
                        f'text-anchor="middle">{escape(title)}</text>')
            return
        body.append(f'<text x="{x:.1f}" y="{top - 24}" font-size="12"{weight} '
                    f'text-anchor="middle">{escape(title)}</text>')
        body.append(f'<text x="{x:.1f}" y="{top - 10}" font-size="11" class="muted" '
                    f'text-anchor="middle">{escape(sub)}</text>')

    for j, column in enumerate(matrix.columns):
        x = label_w + j * (cell_w + gap) + cell_w / 2
        head(x, column.task, (column.mode or "?") if matrix.split else None)
    for k, total in enumerate(matrix.totals):
        head(total_x + k * (cell_w + gap) + cell_w / 2, total.label, None, bold=True)
    for k, judge in enumerate(matrix.judges):
        x = judge_x + k * (cell_w + gap) + cell_w / 2
        name = judge.judge.rsplit("/", 1)[-1]
        if matrix.split:
            name += f" · {judge.column.mode or '?'}"
        body.append(f'<text x="{x:.1f}" y="{top - 24}" font-size="11" class="muted" '
                    f'text-anchor="middle">Judge: unbrauchbar</text>')
        body.append(f'<text x="{x:.1f}" y="{top - 10}" font-size="12" text-anchor="middle">'
                    f"{escape(name)}</text>")
    uniform = _uniform_n(report) is not None

    def cell(x: float, y: float, value: float | None, tip: str, label: str) -> None:
        body.append(f"<g><title>{escape(tip)}</title>")
        if value is None:
            body.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="3" fill="none" '
                        f'stroke="var(--grid)" stroke-dasharray="3 3"/>')
            body.append(f'<text x="{x + cell_w / 2}" y="{y + 20}" font-size="12" class="muted" '
                        f'text-anchor="middle">—</text>')
        else:
            fill, ink = _ramp(value)
            body.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="3" fill="{fill}"/>')
            body.append(f'<text x="{x + cell_w / 2}" y="{y + 20}" font-size="12" text-anchor="middle" '
                        f'style="fill:{ink}">{escape(label)}</text>')
        body.append("</g>")

    for i, row in enumerate(matrix.rows):
        y = top + i * (cell_h + gap)
        body.append(f'<text x="{label_w - 10}" y="{y + 20}" font-size="13" text-anchor="end">'
                    f"{escape(row.model)}</text>")
        for j, column in enumerate(matrix.columns):
            c = row.cells.get(column)
            x = label_w + j * (cell_w + gap)
            if c is None:
                cell(x, y, None, f"{row.model} · {column.label}: nicht gelaufen", "")
            else:
                source = f" ({row.sources[column]})" if matrix.split else ""
                tip = (f"{row.model} · {column.label}: {c.value:.3f}, schlechtester von "
                       f"{c.n} Lauf/Läufen je Datensatz{source}")
                cell(x, y, c.value, tip, _cell_text(c, uniform))
        for k, total in enumerate(matrix.totals):
            overall = row.overall(total)
            x = total_x + k * (cell_w + gap)
            if overall is None:
                cell(x, y, None, f"{row.model} · {total.label}: Aufgabe nur in anderem Modus", "")
            else:
                cell(x, y, overall.value, f"{row.model} · {total.label}: {overall.value:.3f}",
                     _overall_text(overall))
        for k, judge in enumerate(matrix.judges):
            jc = row.judge_cells.get((judge.column, judge.judge))
            x = judge_x + k * (cell_w + gap)
            tip = f"{row.model} · {judge.label}"
            if jc is None or jc.mean is None:
                cell(x, y, None, tip + (": kein lesbares Urteil" if jc else ": nicht gewertet"),
                     "")
            else:
                # Dieselbe Rampe wie die Scores, mit derselben Richtung: dunkel
                # ist gut. Ein kleiner Anteil verworfener Titel ist gut, also
                # 1 − Anteil.
                cell(x, y, 1 - jc.mean,
                     f"{tip}: {jc.mean:.0%} unbrauchbar, aus {jc.n} Lauf/Läufen",
                     _judge_text(row, judge))
    for k, text in enumerate(footer):
        body.append(f'<text x="0" y="{rows_end + 18 + 16 * k}" font-size="11" class="muted">'
                    f"{escape(text)}</text>")
    return _svg(width, height, title, body)


# ---------------------------------------------------------------- Aufruf


# ------------------------------------------------ Diagramme je Aufgabe
#
# Für die Einzelseiten einer Aufgabe: dieselben Zellen wie in der Tabelle, als
# Balken und, mit Dateigrößen der Modelle, gegen die Größe aufgetragen.


def _task_runs(report: Report, task: str) -> list[Run]:
    return [run for run in report.runs if run.task == task]


def _task_columns(matrix: Matrix, task: str) -> list[Column]:
    return [c for c in matrix.columns if c.task == task]


def _bar_path(x: float, y: float, w: float, h: float, fill: str) -> str:
    """Ein Balken ab der Nulllinie, rund nur am Datenende."""
    r = min(4, w)
    return (f'<path d="M{x},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z" fill="{fill}"/>')


def render_svg_task(report: Report, task: str) -> str:
    """Score je Modell in einer Aufgabe als waagerechte Balken, Achse fest von 0 bis 1.

    Mit Denkmodi bekommt jedes Modell einen Balken je Modus. Ein Modell ohne
    Lauf in dieser Aufgabe fehlt, statt als leere Zeile dazustehen.
    """
    matrix = sections(report)[0][1]
    columns = _task_columns(matrix, task)
    rows = [r for r in matrix.rows if any(c in r.cells for c in columns)]
    rows.sort(key=lambda r: (tuple((0, -r.cells[c].value) if c in r.cells else (1, 0.0)
                                   for c in columns), r.model))
    counts = {r.cells[c].n for r in rows for c in columns if c in r.cells}
    uniform = len(counts) == 1
    many = len(columns) > 1
    label_w = max(len(r.model) for r in rows) * CHAR + 24
    plot_w, bar_h, top = 420, 16, 40
    pitch = bar_h + 4
    row_h = 30 if not many else pitch * len(columns) + 10
    axis_y = top + row_h * len(rows)
    n = counts.pop() if uniform else None
    runs = ("ein Lauf" if n == 1 else f"je Datensatz der schlechteste von {n} Läufen" if n
            else "je Datensatz der schlechteste Lauf, Zahl der Läufe in Klammern")
    footer = [f"Score = Mittel über die Datensätze, {runs}.", _footer_line(_task_runs(report, task))]
    width = max(label_w + plot_w + 120, max(len(f) for f in footer) * 6.2)
    height = axis_y + 34 + 16 * (len(footer) - 1)
    title = next(t.display_name for t in report.tasks if t.name == task) + _mode_title(columns)
    body = [f'<text x="0" y="18" font-size="15" font-weight="600">{escape(title)}</text>']
    for tick in (0, 0.25, 0.5, 0.75, 1):
        x = label_w + tick * plot_w
        body.append(f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{axis_y}" stroke="var(--grid)"/>')
        body.append(f'<text x="{x:.1f}" y="{axis_y + 16}" font-size="11" class="muted" '
                    f'text-anchor="middle">{tick:g}</text>')
    for i, row in enumerate(rows):
        y0 = top + i * row_h + (row_h - pitch * len(columns) + 4) / 2
        body.append(f'<text x="{label_w - 10}" y="{y0 + (pitch * len(columns) - 4) / 2 + 5:.1f}" '
                    f'font-size="13" text-anchor="end">{escape(row.model)}</text>')
        for k, column in enumerate(columns):
            y = y0 + k * pitch
            prefix = f"{column.mode or '?'} " if many else ""
            cell = row.cells.get(column)
            if cell is None:
                body.append(f'<text x="{label_w + 6}" y="{y + bar_h - 3}" font-size="12" '
                            f'class="muted">{escape(prefix)}—</text>')
                continue
            w = max(cell.value * plot_w, 0.0)
            note = "" if uniform else f" (n={cell.n})"
            tip = (f"{row.sources[column]} · {column.label}: {cell.value:.3f}, "
                   f"schlechtester von {cell.n} Lauf/Läufen je Datensatz")
            body.append(f"<g><title>{escape(tip)}</title>")
            if w > 0:
                body.append(_bar_path(label_w, y, w, bar_h, "var(--bar)"))
            body.append(f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h - 3}" font-size="12">'
                        f'{escape(prefix)}{cell.value:.2f}<tspan class="muted">{escape(note)}'
                        "</tspan></text>")
            body.append("</g>")
    for k, text in enumerate(footer):
        body.append(f'<text x="0" y="{axis_y + 30 + 16 * k}" font-size="11" class="muted">'
                    f"{escape(text)}</text>")
    return _svg(width, height, title, body)


# Spalten der Datei mit den Modellgrößen. Weitere Spalten (Quantisierung,
# Parameterzahl …) sind erlaubt und werden übergangen.
SIZE_COLUMNS = ("modell", "dateigroesse_gb")


def read_model_sizes(path: Path) -> dict[str, float]:
    """Dateigröße in GB je Modellname, so wie der Name in der Tabelle steht.

    Die Größe steht in keinem Log: llama-server meldet den Pfad der Datei,
    nicht ihre Größe, und die Datei liegt auf dem Server, nicht hier. Getrennt
    werden Spalten mit Komma oder Semikolon, Dezimalstellen mit Punkt oder
    Komma (dann mit Semikolon als Trenner, wie aus einer deutschen
    Tabellenkalkulation).
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ReportError(f"Modellgrößen nicht lesbar: {path} ({error.strerror})")
    first = text.splitlines()[0] if text.strip() else ""
    reader = csv.DictReader(text.splitlines(), delimiter=";" if ";" in first else ",")
    missing = [c for c in SIZE_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ReportError(f"{path}: Spalte fehlt: {', '.join(missing)} "
                          f"(erwartet: {', '.join(SIZE_COLUMNS)})")
    sizes: dict[str, float] = {}
    for line, entry in enumerate(reader, start=2):
        model = (entry["modell"] or "").strip()
        raw = (entry["dateigroesse_gb"] or "").strip()
        if not model or not raw:
            continue
        try:
            size = float(raw.replace(",", "."))
        except ValueError:
            raise ReportError(f"{path}, Zeile {line}: keine Zahl: {raw!r}")
        if size <= 0:
            raise ReportError(f"{path}, Zeile {line}: Dateigröße muss größer als 0 sein: {raw!r}")
        if model in sizes:
            raise ReportError(f"{path}, Zeile {line}: {model} steht doppelt")
        sizes[model] = size
    return sizes


# Je Reihe Farbe und Form. Die Form trägt den Unterschied auch ohne Farbe.
SERIES_STYLE = (("var(--bar)", "circle"), ("var(--accent)", "square"), ("var(--ink2)", "diamond"))


def _marker(x: float, y: float, shape: str, fill: str) -> str:
    if shape == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{fill}"/>'
    if shape == "square":
        return f'<rect x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" fill="{fill}"/>'
    return (f'<path d="M{x:.1f},{y - 6:.1f} l6,6 l-6,6 l-6,-6 z" fill="{fill}"/>')


def _log_ticks(lo: float, hi: float) -> list[float]:
    """1-2-5-Schritte im sichtbaren Bereich."""
    ticks = []
    for exponent in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
        for step in (1, 2, 5):
            value = step * 10.0 ** exponent
            if lo <= value <= hi:
                ticks.append(value)
    return ticks


def render_svg_scatter(report: Report, sizes: dict[str, float], task: str | None = None) -> str:
    """Score nach oben gegen die Dateigröße nach rechts, logarithmisch.

    Ohne `task` der Gesamtscore, sonst der Score dieser Aufgabe. Je
    Gesamtspalte bzw. Denkmodus eine Reihe. Modelle ohne Dateigröße, meist
    Cloud-Modelle, haben keinen Platz auf der Achse. Sie stehen als
    gestrichelte Linie in Höhe ihres Scores darin, als Vergleichsmaß.
    """
    matrix = sections(report)[0][1]
    series: list[tuple[str, dict[str, float]]] = []
    if task is None:
        for total in matrix.totals:
            values = {r.model: o.value for r in matrix.rows if (o := r.overall(total))}
            series.append((total.label, values))
        title = "Gesamtscore nach Dateigröße" + _mode_title(matrix.columns)
        runs = report.runs
    else:
        for column in _task_columns(matrix, task):
            values = {r.model: r.cells[column].value for r in matrix.rows if column in r.cells}
            series.append((column.mode or "?" if matrix.split else column.task, values))
        title = (next(t.display_name for t in report.tasks if t.name == task) + " nach Dateigröße"
                 + _mode_title(_task_columns(matrix, task)))
        runs = _task_runs(report, task)
    measured = {model for _, values in series for model in values}
    unsized = sorted(m for m in measured if m not in sizes)
    shown = [sizes[m] for m in measured if m in sizes]

    left, top, plot_w, plot_h = 52, 44 if len(series) > 1 else 36, 520, 300
    label_room = 190
    bottom = top + plot_h
    footer = ["Je Punkt ein Modell; Score wie in der Tabelle."]
    if unsized:
        footer.append("Gestrichelt: Modelle ohne Dateigröße (" + ", ".join(unsized) + ")")
    footer.append(_footer_line(runs))
    width = max(left + plot_w + label_room, max(len(f) for f in footer) * 6.2)
    height = bottom + 46 + 16 * len(footer)
    body = [f'<text x="0" y="18" font-size="15" font-weight="600">{escape(title)}</text>']
    if not shown:
        body.append(f'<text x="{left}" y="{top + 20}" font-size="12" class="muted">'
                    "Zu keinem Modell dieser Tabelle ist eine Dateigröße angegeben.</text>")
        lo, hi = 1.0, 10.0
    else:
        lo, hi = min(shown) / 1.3, max(shown) * 1.3

    def px(size: float) -> float:
        return left + (math.log(size) - math.log(lo)) / (math.log(hi) - math.log(lo)) * plot_w

    def py(value: float) -> float:
        return bottom - value * plot_h

    for tick in (0, 0.25, 0.5, 0.75, 1):
        y = py(tick)
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="var(--grid)"/>')
        body.append(f'<text x="{left - 8}" y="{y + 4:.1f}" font-size="11" class="muted" '
                    f'text-anchor="end">{tick:g}</text>')
    for tick in _log_ticks(lo, hi):
        x = px(tick)
        body.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="var(--grid)"/>')
        body.append(f'<text x="{x:.1f}" y="{bottom + 16}" font-size="11" class="muted" '
                    f'text-anchor="middle">{tick:g}</text>')
    body.append(f'<text x="{left + plot_w / 2}" y="{bottom + 34}" font-size="12" class="muted" '
                'text-anchor="middle">Dateigröße in GB (logarithmisch)</text>')
    body.append(f'<text x="14" y="{top + plot_h / 2}" font-size="12" class="muted" text-anchor="middle" '
                f'transform="rotate(-90 14 {top + plot_h / 2})">Score</text>')
    if len(series) > 1:
        x = left
        for k, (label, _) in enumerate(series):
            fill, shape = SERIES_STYLE[k % len(SERIES_STYLE)]
            body.append(_marker(x + 5, top - 16, shape, fill))
            body.append(f'<text x="{x + 14}" y="{top - 12}" font-size="12">{escape(label)}</text>')
            x += 14 + len(label) * 7 + 18

    # Beschriftung rechts neben dem Punkt; überdeckt sie eine schon gesetzte,
    # rutscht sie zeilenweise nach unten.
    placed: list[tuple[float, float, float]] = []
    points = []
    for k, (label, values) in enumerate(series):
        for model, value in values.items():
            if model in sizes:
                points.append((py(value), px(sizes[model]), k, label, model, value))
    for k, (label, values) in enumerate(series):
        fill = SERIES_STYLE[k % len(SERIES_STYLE)][0]
        for model in unsized:
            if model not in values:
                continue
            y = py(values[model])
            name = model.rsplit("/", 1)[-1] + (f" · {label}" if len(series) > 1 else "")
            lx, ly, lw = left + plot_w + 6, y + 4, len(name) * 6.6
            while any(abs(ly - oy) < 12 and lx < ox + ow and ox < lx + lw for ox, oy, ow in placed):
                ly += 12
            placed.append((lx, ly, lw))
            tip = f"{model} · {label}: {values[model]:.3f}, ohne Dateigröße"
            body.append(f'<g><title>{escape(tip)}</title><line x1="{left}" y1="{y:.1f}" '
                        f'x2="{left + plot_w}" y2="{y:.1f}" stroke="{fill}" stroke-width="1.5" '
                        f'stroke-dasharray="5 4"/><text x="{lx:.1f}" y="{ly:.1f}" font-size="11" '
                        f'class="muted halo">{escape(name)}</text></g>')
    for y, x, k, label, model, value in sorted(points):
        fill, shape = SERIES_STYLE[k % len(SERIES_STYLE)]
        name = model.rsplit("/", 1)[-1] + (f" · {label}" if len(series) > 1 else "")
        lx, ly, lw = x + 9, y + 4, len(name) * 6.6
        while any(abs(ly - oy) < 12 and lx < ox + ow and ox < lx + lw for ox, oy, ow in placed):
            ly += 12
        placed.append((lx, ly, lw))
        tip = f"{model} · {label}: {value:.3f}, {sizes[model]:g} GB"
        body.append(f"<g><title>{escape(tip)}</title>{_marker(x, y, shape, fill)}"
                    f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" class="halo">{escape(name)}</text></g>')
    for k, text in enumerate(footer):
        body.append(f'<text x="0" y="{bottom + 58 + 16 * k}" font-size="11" class="muted">'
                    f"{escape(text)}</text>")
    return _svg(width, height, title, body)


def _not_comparable(problems: list[str]) -> str:
    return "Läufe derselben Aufgabe sind nicht vergleichbar:\n\n" + "\n\n".join(problems)


def build(log_dir: Path, pattern: str = "*.eval", since: datetime | None = None) -> Report:
    files = select_logs(log_dir, pattern, since)
    runs, failed = read_runs(files)
    if not runs:
        raise ReportError("Kein Lauf der Auswahl hat ein Ergebnis:\n"
                          + "\n".join(f"  {line}" for line in failure_lines(failed))
                          + "\n\n" + view_hint(log_dir))
    judged, foreign = read_judges(files, runs)
    messages = []
    if problems := check_consistency(runs) + check_modes(runs) + check_judges(judged):
        messages.append(_not_comparable(problems))
    if foreign:
        messages.append("Judge-Dateien, die nicht zum Log daneben passen:\n"
                        + "\n".join(f"  {line}" for line in foreign))
    if messages:
        raise ReportError(
            "\n\n".join(messages)
            + "\n\nDie Auswahl (--glob, --since) so fassen, dass nur vergleichbare Läufe "
            "darin liegen."
        )
    # Erst nach allen Prüfungen: Die Logs ganz zu lesen kostet Zeit, und eine
    # Auswahl, die ohnehin abbricht, soll das nicht abwarten müssen.
    answers: list[Answer] = []
    stats: list[RunStats] = []
    for run in runs:
        log = read_eval_log(str(log_dir / run.file), resolve_attachments=True)
        answers += log_answers(log)
        stats.append(run_stats(log, run.file))
    report = aggregate(runs, answers, judged)
    report.stats = stats
    report.failed = failed
    report.selection = {
        "log_dir": str(log_dir),
        "glob": pattern,
        "since": since.isoformat() if since else None,
        "files": [p.name for p in files],
    }
    return report


def write_outputs(report: Report, out: Path, sizes: dict[str, float] | None = None) -> list[Path]:
    """Alle Dateien des Berichts; die Streudiagramme nur mit Modellgrößen."""
    out.mkdir(parents=True, exist_ok=True)
    failed = render_failed(report)
    markdown = (render_markdown(report) + "\n" + (failed + "\n" if failed else "")
                + render_runstats(report) + "\n"
                + render_details(report, report.answers, out))
    written = {
        "report.md": markdown,
        "report.html": render_html(markdown),
        "report.json": render_json(report),
        "overall.svg": render_svg_overall(report),
        "matrix.svg": render_svg_matrix(report),
    }
    for task in report.tasks:
        written[f"bars-{task.name}.svg"] = render_svg_task(report, task.name)
    if sizes is not None:
        written["scatter.svg"] = render_svg_scatter(report, sizes)
        for task in report.tasks:
            written[f"scatter-{task.name}.svg"] = render_svg_scatter(report, sizes, task.name)
    paths = []
    for name, text in written.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def parse_since(value: str) -> datetime:
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"kein Datum: {value!r} (erwartet z. B. 2026-10-03 oder 2026-10-03T11:40)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.report",
        description="Fasst .eval-Läufe zu Tabellen zusammen: Score je Aufgabe und Modell, "
        "Gesamtscore je Modell. Gewertet wird nichts neu — jede Zahl ist der Leitwert, "
        "den die Aufgabe selbst ins Log geschrieben hat.",
    )
    parser.add_argument("log_dir", nargs="?", default="logs", type=Path,
                        help="Verzeichnis mit .eval-Dateien (Standard: logs)")
    parser.add_argument("--glob", default="*.eval",
                        help="nur Dateien, deren Name passt, z. B. '*m01-titles*' (Standard: *.eval)")
    parser.add_argument("--since", type=parse_since,
                        help="nur Läufe ab diesem Zeitpunkt; ohne Zeitzone gilt UTC wie in den Dateinamen")
    parser.add_argument("--out", type=Path,
                        help="schreibt report.md und report.html (Tabelle, abgebrochene Läufe, Laufdaten "
                        "und jede Antwort mit Wertung), "
                        "report.json, overall.svg, matrix.svg und je Aufgabe bars-<aufgabe>.svg "
                        "dorthin")
    parser.add_argument("--model-sizes", type=Path, metavar="CSV",
                        help="Dateigröße je Modell (Spalten modell,dateigroesse_gb); mit --out "
                        "entstehen dann scatter.svg und je Aufgabe scatter-<aufgabe>.svg")
    args = parser.parse_args(argv)
    if args.model_sizes and not args.out:
        parser.error("--model-sizes braucht --out")

    try:
        sizes = read_model_sizes(args.model_sizes) if args.model_sizes else None
        report = build(args.log_dir, args.glob, args.since)
    except ReportError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    print(render_terminal(report))
    if args.out:
        print()
        for path in write_outputs(report, args.out, sizes):
            print(f"geschrieben: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
