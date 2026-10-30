"""Trennschärfe je Datensatz — aus den Logs, nicht aus dem Gefühl.

Der Oracle-Lauf beweist, dass die WERTUNG funktioniert. Er beweist nie, dass
ein Datensatz etwas MISST. Ein Item, das jedes Modell löst, hebt den Wert um
einen festen Betrag und sieht in jedem einzelnen Lauf wie Können aus; eines,
das keines löst, sieht wie ein schweres Item aus und ist womöglich eine falsch
notierte Referenz. Beides wird erst sichtbar, wenn man dieselbe Aufgabe über
mehrere Modelle auszählt.

Vier Fragen, beantwortet aus den `.eval`-Logs:

  1. wo trifft KEIN Modell je        -> Referenz-Verdacht, von Hand prüfen
  2. wo trifft JEDES Modell immer    -> keine Trennschärfe, Streichkandidat
  3. wo schwankt DASSELBE Modell     -> wackeliges Item, Einzelmessung wertlos
  4. wie schwer war ein Item wirklich -> Grundlage der Stufeneinteilung

    uv run python -m german_seo_bench.itemcheck logs/*.eval
    uv run python -m german_seo_bench.itemcheck logs/*.eval --task m01_titles

Jedes Modell zählt gleich schwer, unabhängig davon, wie oft es gelaufen ist.
Sonst kippt ein Modell mit fünf Läufen die Statistik gegen eines mit einem —
und die Aussage wäre nicht "dieses Item ist leicht", sondern "das Modell, das
am häufigsten lief, kann es".

Das hier ist ausdrücklich KEINE Hülle um `inspect eval`: Es reicht keine seiner
Optionen durch und verdeckt nichts. Es beantwortet eine Frage, die Inspect
nicht stellt, weil sie nicht einen Lauf betrifft, sondern deren Menge.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple


class Observation(NamedTuple):
    """Ein Modell, ein Datensatz, ein Versuch."""

    model: str
    item: str
    passed: bool
    level: int | str | None = None


# Unter drei Modellen wird kein Befund ausgesprochen. Ein Datensatz, den EIN
# Modell nie löst, ist kein Referenz-Verdacht, sondern ein schweres Item; einer,
# den ein Modell immer löst, ist nicht stumpf. Genau diese Verwechslung wäre die
# teuerste Fehlbedienung des Werkzeugs: Sie führt dazu, dass man eine korrekte
# Referenz "korrigiert".
MIN_MODELS = 3


@dataclass
class ItemStat:
    """Was über einen Datensatz aus allen Läufen zusammen bekannt ist."""

    item: str
    level: int | str | None
    rate: float
    """Mittel über die Modelle, jedes Modell gleich gewichtet."""
    per_model: dict[str, float]
    attempts: int
    wobbly: list[str]
    """Modelle, die denselben Datensatz mal lösen und mal nicht."""

    @property
    def models(self) -> int:
        return len(self.per_model)

    @property
    def verdict(self) -> str:
        # "wackelig" gilt auch bei einem Modell: Dass dasselbe Modell denselben
        # Datensatz mal löst und mal nicht, ist eine Aussage über das Item,
        # egal wie viele andere Modelle daneben gelaufen sind.
        if self.wobbly:
            return "wackelig"
        if self.models < MIN_MODELS:
            return ""
        if self.rate == 0.0:
            return "Referenz?"
        if self.rate == 1.0:
            return "stumpf"
        return ""


def summarize(observations: Iterable[Observation]) -> list[ItemStat]:
    """Die Auszählung. Rein, ohne Inspect — deshalb einzeln testbar.

    Reihenfolge: absteigend nach Bestehensquote, bei Gleichstand nach Name.
    Damit ist die Ausgabe zwischen zwei Aufrufen stabil und taugt als Grundlage
    einer Stufeneinteilung, die man später wiederholen können muss.
    """
    attempts: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    levels: dict[str, int | str | None] = {}
    for observation in observations:
        attempts[observation.item][observation.model].append(observation.passed)
        levels.setdefault(observation.item, observation.level)

    stats: list[ItemStat] = []
    for item, by_model in attempts.items():
        per_model = {
            model: sum(tries) / len(tries) for model, tries in by_model.items()
        }
        stats.append(
            ItemStat(
                item=item,
                level=levels.get(item),
                # Mittel über die MODELLE, nicht über die Versuche.
                rate=sum(per_model.values()) / len(per_model),
                per_model=per_model,
                attempts=sum(len(tries) for tries in by_model.values()),
                wobbly=sorted(
                    model for model, rate in per_model.items() if 0.0 < rate < 1.0
                ),
            )
        )
    return sorted(stats, key=lambda s: (-s.rate, s.item))


def suggest_levels(stats: list[ItemStat], bands: int = 5) -> dict[str, int]:
    """Stufenvorschlag: gleich große Bänder entlang der gemessenen Schwierigkeit.

    Bewusst gleich große Bänder und keine festen Schwellen: Eine Zusicherung
    verlangt mindestens fünf Datensätze je Stufe, und Schwellen liefern bei
    einer schiefen Verteilung Stufen mit zwei Items. Gleichstände fallen nach
    Name, damit derselbe Datenbestand denselben Vorschlag ergibt.

    Der Vorschlag ist eine Schwierigkeit FÜR DIE GEMESSENEN MODELLE. Wer ihn
    übernimmt, friert damit ein, was heute schwer war — deshalb gehört er
    nachgerechnet, sobald stärkere Modelle dazukommen.
    """
    ordered = [s.item for s in stats]
    if not ordered:
        return {}
    size, remainder = divmod(len(ordered), bands)
    levels: dict[str, int] = {}
    start = 0
    for band in range(bands):
        width = size + (1 if band < remainder else 0)
        for item in ordered[start : start + width]:
            levels[item] = band + 1
        start += width
    return levels


def current_levels(task: str) -> dict[str, int | str]:
    """Die Stufen aus der HEUTIGEN Datendatei, nicht die aus dem Log.

    Ein Log trägt die Stufe, die zur Laufzeit galt. Genau die will man hier
    nicht: Die Frage lautet "passt die heutige Einteilung zu dem, was gemessen
    wurde", und dafür muss die eine Seite des Vergleichs aktuell sein. Logs
    verschiedener Aufgabenversionen trügen sonst verschiedene Einteilungen
    nebeneinander — eine Spalte, die aussieht wie eine Einteilung und in
    Wahrheit die Versionsgeschichte zeigt.
    """
    import json
    from importlib import resources

    try:
        raw = resources.files("german_seo_bench.data").joinpath(f"{task}.json").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return {}
    return {
        record["id"]: record["reference"]["level"]
        for record in json.loads(raw)["samples"]
        if "level" in record.get("reference", {})
    }


def read_logs(paths: list[Path], task: str | None = None) -> dict[tuple[str, int], list[Observation]]:
    """Beobachtungen je (Aufgabe, Aufgabenversion) aus den `.eval`-Dateien.

    Nach Version getrennt, und das ist keine Förmlichkeit: Zahlen aus zwei
    Aufgabenversionen unter einem Namen zusammenzuzählen ist genau der Fehler,
    gegen den `Task.version` überhaupt existiert.

    Oracle-Läufe (`none/none`) fliegen raus. Sie sind per Konstruktion 1.00 und
    würden jeden Datensatz als "stumpf" melden.
    """
    from inspect_ai.log import read_eval_log

    from german_seo_bench.modelname import model_label

    collected: dict[tuple[str, int], list[Observation]] = defaultdict(list)
    for path in paths:
        log = read_eval_log(str(path))
        name = log.eval.task.split("/")[-1]
        if task and name != task:
            continue
        if log.eval.model.endswith("none/none") or log.eval.model == "none":
            continue
        if not log.samples:
            continue
        for sample in log.samples:
            score = next(iter(sample.scores.values()), None) if sample.scores else None
            if score is None or not isinstance(score.value, dict):
                continue
            if "score" not in score.value:
                continue
            collected[(name, log.eval.task_version or 0)].append(
                Observation(
                    model=model_label(log),
                    item=str(sample.id),
                    passed=float(score.value["score"]) >= 1.0,
                )
            )
    return collected


def render(key: tuple[str, int], observations: list[Observation]) -> str:
    """Die Tabelle für eine Aufgabe in einer Version."""
    stats = summarize(observations)
    models = sorted({o.model for o in observations})
    proposal = suggest_levels(stats)
    levels = current_levels(key[0])
    lines = [
        f"{key[0]}  (Aufgabenversion {key[1]})",
        f"  {len(models)} Modelle, {len(stats)} Datensätze, "
        f"{sum(s.attempts for s in stats)} Antworten",
        "",
        f"  {'Datensatz':26}{'Quote':>7}{'Stufe':>7}{'Vorschlag':>10}  Befund",
    ]
    for stat in stats:
        level = str(levels.get(stat.item, "—"))
        lines.append(
            f"  {stat.item:26}{stat.rate:7.2f}{level:>7}"
            f"{proposal[stat.item]:>10}  {stat.verdict}"
        )

    if len(models) < MIN_MODELS:
        lines += [
            "",
            f"  Nur {len(models)} Modell(e): zu wenig für ein Urteil über die "
            f"Datensätze. Ab {MIN_MODELS} Modellen werden Befunde ausgewiesen —",
            "  bis dahin ist eine Quote von 0.00 ein schweres Item und kein "
            "Referenzfehler.",
        ]

    for label, hits in (
        ("Referenz-Verdacht (kein Modell trifft je)", [s for s in stats if s.verdict == "Referenz?"]),
        ("ohne Trennschärfe (jedes Modell trifft immer)", [s for s in stats if s.verdict == "stumpf"]),
        ("wackelig (dasselbe Modell schwankt)", [s for s in stats if s.wobbly]),
    ):
        lines += ["", f"  {label}: {len(hits)}"]
        for stat in hits:
            note = f"  ({', '.join(stat.wobbly)})" if stat.wobbly else ""
            lines.append(f"    {stat.item}{note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.itemcheck",
        description="Trennschärfe je Datensatz aus den .eval-Logs.",
    )
    parser.add_argument("logs", nargs="+", type=Path, help="Log-Dateien (.eval)")
    parser.add_argument("--task", help="nur diese Aufgabe, z.B. m01_titles")
    args = parser.parse_args(argv)

    collected = read_logs(args.logs, args.task)
    if not collected:
        print("Keine auswertbaren Läufe. Oracle-Läufe zählen nicht mit.", file=sys.stderr)
        return 1
    for key in sorted(collected):
        print(render(key, collected[key]))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
