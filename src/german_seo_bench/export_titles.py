"""`python -m german_seo_bench.export_titles` — Titel aus M01-Logs als CSV zum Bewerten.

Grundlage für die Kalibrierung des Titel-Judges (`m01_judge`). Die CSV
hat genau die Spalten `title,human_score`. `human_score` bleibt leer und wird
von Hand mit der menschlichen Klasse gefüllt: 1 = unbrauchbar, 2 = vielleicht,
3 = brauchbar (`tasks/m01_judge.py`).

Aufgenommen wird ein Titel nur, wenn der M01-Scorer im Log `format` und
`german` als bestanden vermerkt hat. Ein Titel mit Markdown oder auf Englisch
ist für die Frage „liest er sich natürlich?“ kein sinnvoller Fall, und das
schließt der Code ohnehin aus. Neu geprüft wird hier nichts. Gilt der Wert des
Scorers im Log, gibt es nur eine Stelle, die über einen Titel entscheidet.

Die Titel werden dedupliziert und mit festem Seed gemischt. Sortiert nach Lauf
stünden die Titel eines Modells beieinander, und wer bewertet, sähe am Stil,
von welchem Modell sie stammen. Mit festem Seed ergibt derselbe Export dieselbe
Datei.
"""

import argparse
import csv
import random
import sys
from pathlib import Path

from inspect_ai.log import read_eval_log

from german_seo_bench.report import ReportError, parse_since, select_logs
from german_seo_bench.tasks.m01_judge import COLUMNS

# Die Prüfungen, die bestanden sein müssen. Dieselben Schlüssel wie im
# M01-Score; fehlt einer im Log (andere Aufgabenversion), zählt der Titel als
# nicht bestanden. Ob er die Prüfung bestanden hätte, ist dann unbekannt.
REQUIRED_CHECKS = ("format", "german")

SEED = 20261005


def collect_titles(files: list[Path]) -> tuple[list[str], dict[str, int]]:
    """Alle eindeutigen Titel, die die Prüfungen bestanden haben, dazu eine Zählung.

    Die Zählung nennt, was nicht in der Datei landet. Ein Export, der nur sagt
    „562 Titel“, verschweigt, ob 30 oder 3000 Antworten verworfen wurden.
    """
    titles: dict[str, None] = {}
    counts = {"logs": 0, "logs_without_result": 0, "answers": 0, "empty": 0, "failed_checks": 0}
    for path in files:
        log = read_eval_log(str(path))
        if log.status != "success" or not log.samples:
            counts["logs_without_result"] += 1
            continue
        counts["logs"] += 1
        for sample in log.samples:
            score = (sample.scores or {}).get("title_scorer")
            if score is None or not isinstance(score.value, dict):
                continue
            counts["answers"] += 1
            title = sample.output.completion.strip()
            if not title:
                counts["empty"] += 1
                continue
            if not all(score.value.get(check) == 1 for check in REQUIRED_CHECKS):
                counts["failed_checks"] += 1
                continue
            titles.setdefault(title, None)
    unique = list(titles)
    random.Random(SEED).shuffle(unique)
    counts["unique"] = len(unique)
    return unique, counts


def write_csv(titles: list[str], out: Path) -> None:
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for title in titles:
            writer.writerow([title, ""])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.export_titles",
        description="Schreibt die Titel aus M01-Logs als CSV (title,human_score) zum "
        "Bewerten. Aufgenommen wird, was der M01-Scorer als format- und german-gültig "
        "vermerkt hat; dedupliziert und gemischt.",
    )
    parser.add_argument("log_dir", nargs="?", default="logs", type=Path,
                        help="Verzeichnis mit .eval-Dateien (Standard: logs)")
    parser.add_argument("--glob", default="*m01-titles*.eval",
                        help="Auswahl der Dateien (Standard: *m01-titles*.eval)")
    parser.add_argument("--since", type=parse_since,
                        help="nur Läufe ab diesem Zeitpunkt; ohne Zeitzone gilt UTC")
    parser.add_argument("--out", type=Path, required=True, help="Ziel-CSV")
    args = parser.parse_args(argv)

    try:
        files = select_logs(args.log_dir, args.glob, args.since)
    except ReportError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    if args.out.exists():
        # Eine überschriebene Datei mit halb fertiger Handbewertung wäre ein
        # Abend Arbeit. Lieber abbrechen und die Wahl dem Menschen lassen.
        print(f"Fehler: {args.out} existiert schon und wird nicht überschrieben.", file=sys.stderr)
        return 1

    titles, counts = collect_titles(files)
    write_csv(titles, args.out)
    print(
        f"{counts['unique']} Titel nach {args.out} geschrieben "
        f"(aus {counts['answers']} Antworten in {counts['logs']} Läufen).\n"
        f"Nicht aufgenommen: {counts['empty']} leer, "
        f"{counts['failed_checks']} an format/german gescheitert, "
        f"{counts['answers'] - counts['empty'] - counts['failed_checks'] - counts['unique']} doppelt; "
        f"{counts['logs_without_result']} Logs ohne Ergebnis übersprungen."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
